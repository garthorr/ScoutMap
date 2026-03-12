"""Scout form field configuration — admin CRUD + public read."""

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import ScoutFormField
from app.routes.auth import require_admin

router = APIRouter(prefix="/api/form-fields", tags=["form-fields"])

VALID_TYPES = {"toggle", "checkbox", "text", "number", "textarea", "select"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class FormFieldCreate(BaseModel):
    label: str
    field_type: str
    required: bool = False
    options: Optional[list[str]] = None  # for select type


class FormFieldUpdate(BaseModel):
    label: Optional[str] = None
    field_type: Optional[str] = None
    required: Optional[bool] = None
    position: Optional[int] = None
    options: Optional[list[str]] = None
    active: Optional[bool] = None


class FormFieldOut(BaseModel):
    id: str
    field_key: str
    label: str
    field_type: str
    required: bool
    position: int
    options: Optional[list[str]] = None
    active: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slugify(label: str) -> str:
    """Convert label to a snake_case key."""
    s = label.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "field"


def _field_to_out(f: ScoutFormField) -> FormFieldOut:
    options = None
    if f.options:
        try:
            options = json.loads(f.options)
        except (json.JSONDecodeError, TypeError):
            pass
    return FormFieldOut(
        id=str(f.id),
        field_key=f.field_key,
        label=f.label,
        field_type=f.field_type,
        required=f.required,
        position=f.position,
        options=options,
        active=f.active,
    )


# ---------------------------------------------------------------------------
# Public: get active fields (used by scout app)
# ---------------------------------------------------------------------------
@router.get("/", response_model=list[FormFieldOut])
def list_form_fields(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(ScoutFormField)
    if not include_inactive:
        q = q.filter(ScoutFormField.active == True)  # noqa: E712
    fields = q.order_by(ScoutFormField.position, ScoutFormField.created_at).all()
    return [_field_to_out(f) for f in fields]


# ---------------------------------------------------------------------------
# Admin: create field
# ---------------------------------------------------------------------------
@router.post("/", response_model=FormFieldOut)
def create_form_field(
    body: FormFieldCreate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.field_type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid field type. Must be one of: {', '.join(sorted(VALID_TYPES))}")
    if not body.label.strip():
        raise HTTPException(400, "Label is required")
    if body.field_type == "select" and not body.options:
        raise HTTPException(400, "Select fields require at least one option")

    field_key = _slugify(body.label)
    # Ensure unique key
    existing = db.query(ScoutFormField).filter(ScoutFormField.field_key == field_key).first()
    if existing:
        # Append a number to make it unique
        count = db.query(func.count(ScoutFormField.id)).filter(
            ScoutFormField.field_key.like(field_key + "%")
        ).scalar()
        field_key = f"{field_key}_{count}"

    # Position: put at end
    max_pos = db.query(func.max(ScoutFormField.position)).scalar() or 0

    field = ScoutFormField(
        field_key=field_key,
        label=body.label.strip(),
        field_type=body.field_type,
        required=body.required,
        position=max_pos + 1,
        options=json.dumps(body.options) if body.options else None,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return _field_to_out(field)


# ---------------------------------------------------------------------------
# Admin: update field
# ---------------------------------------------------------------------------
@router.put("/{field_id}", response_model=FormFieldOut)
def update_form_field(
    field_id: str,
    body: FormFieldUpdate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = db.query(ScoutFormField).filter(ScoutFormField.id == field_id).first()
    if not field:
        raise HTTPException(404, "Field not found")

    if body.label is not None:
        field.label = body.label.strip()
    if body.field_type is not None:
        if body.field_type not in VALID_TYPES:
            raise HTTPException(400, f"Invalid field type. Must be one of: {', '.join(sorted(VALID_TYPES))}")
        field.field_type = body.field_type
    if body.required is not None:
        field.required = body.required
    if body.position is not None:
        field.position = body.position
    if body.options is not None:
        field.options = json.dumps(body.options)
    if body.active is not None:
        field.active = body.active

    db.commit()
    db.refresh(field)
    return _field_to_out(field)


# ---------------------------------------------------------------------------
# Admin: delete field (keeps data in visits.custom_data)
# ---------------------------------------------------------------------------
@router.delete("/{field_id}")
def delete_form_field(
    field_id: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = db.query(ScoutFormField).filter(ScoutFormField.id == field_id).first()
    if not field:
        raise HTTPException(404, "Field not found")
    db.delete(field)
    db.commit()
    return {"ok": True, "field_key": field.field_key}


# ---------------------------------------------------------------------------
# Admin: reorder fields
# ---------------------------------------------------------------------------
class ReorderBody(BaseModel):
    field_ids: list[str]


@router.put("/reorder/batch")
def reorder_fields(
    body: ReorderBody,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    for idx, fid in enumerate(body.field_ids):
        field = db.query(ScoutFormField).filter(ScoutFormField.id == fid).first()
        if field:
            field.position = idx
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Seed default fields if table is empty
# ---------------------------------------------------------------------------
def seed_default_fields(db: Session):
    """Insert default scout form fields if none exist."""
    if db.query(ScoutFormField).count() > 0:
        return

    defaults = [
        ("door_answer", "Door Answer", "toggle", False, 1),
        ("donation_given", "Donation", "toggle", False, 2),
        ("donation_amount", "Donation Amount", "number", False, 3),
        ("former_scout", "Former Scout?", "toggle", False, 4),
        ("avoid_house", "Avoid This House", "checkbox", False, 5),
        ("notes", "Notes", "textarea", False, 6),
    ]
    for key, label, ftype, req, pos in defaults:
        db.add(ScoutFormField(
            field_key=key, label=label, field_type=ftype,
            required=req, position=pos,
        ))
    db.commit()
