from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import case

db = SQLAlchemy()

def utcnow():
    return datetime.now(timezone.utc)

class Scooter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(40), nullable=False)
    manufacturer = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(80), nullable=False)
    battery_catalog_id = db.Column(db.String(100))
    battery_model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    manufactured_on = db.Column(db.Date)
    purchased_on = db.Column(db.Date)
    notes = db.Column(db.Text, default='')
    registered_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    archived_at = db.Column(db.DateTime(timezone=True))
    schedule = db.relationship('MaintenanceSchedule', uselist=False, backref='scooter')
    inspections = db.relationship('Inspection', backref='scooter', order_by=lambda: (Inspection.inspected_on.desc(), Inspection.created_at.desc(), Inspection.id.desc()))
    replacements = db.relationship('BatteryReplacement', backref='scooter', order_by=lambda: (BatteryReplacement.replaced_on.desc(), BatteryReplacement.created_at.desc(), BatteryReplacement.id.desc()))

    def snapshot(self):
        return {k: getattr(self, k) for k in ('id', 'nickname', 'manufacturer', 'model', 'battery_model', 'serial_number')}

class MaintenanceSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scooter_id = db.Column(db.Integer, db.ForeignKey('scooter.id'), unique=True, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    battery_next_due_on = db.Column(db.Date)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (db.CheckConstraint('weekday >= 0 AND weekday <= 6'),)

class Inspection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scooter_id = db.Column(db.Integer, db.ForeignKey('scooter.id'), nullable=False)
    inspected_on = db.Column(db.Date, nullable=False)
    overall_result = db.Column(db.String(10), nullable=False)
    impact_water_result = db.Column(db.String(10), nullable=False)
    notes = db.Column(db.Text, default='')
    policy_version = db.Column(db.String(20), default='1.1', nullable=False)
    scooter_snapshot_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    submission_token = db.Column(db.String(512), unique=True, nullable=False)
    items = db.relationship('InspectionItem', backref='inspection', cascade='all, delete-orphan', order_by=lambda: case({'tire':0,'brake':1,'battery_body':2,'charger_port':3}, value=InspectionItem.item_key))
    __table_args__ = (db.CheckConstraint("overall_result IN ('ok','issue','unknown')"), db.CheckConstraint("impact_water_result IN ('no','yes','unknown')"))

class InspectionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspection.id'), nullable=False)
    item_key = db.Column(db.String(20), nullable=False)
    result = db.Column(db.String(10), nullable=False)
    criteria_snapshot = db.Column(db.Text, nullable=False)
    __table_args__ = (db.UniqueConstraint('inspection_id', 'item_key'), db.CheckConstraint("result IN ('ok','issue','unknown')"), db.CheckConstraint("item_key IN ('tire','brake','battery_body','charger_port')"))

class BatteryReplacement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scooter_id = db.Column(db.Integer, db.ForeignKey('scooter.id'), nullable=False)
    replaced_on = db.Column(db.Date, nullable=False)
    battery_model = db.Column(db.String(100))
    center_name = db.Column(db.String(100))
    notes = db.Column(db.Text, default='')
    scooter_snapshot_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    origin = db.Column(db.String(20), default='user')

class HistoryAnnotation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    target_type = db.Column(db.String(20), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.CheckConstraint("target_type IN ('inspection','battery')"),)

class ScheduleChange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scooter_id = db.Column(db.Integer, db.ForeignKey('scooter.id'), nullable=False)
    old_weekday = db.Column(db.Integer, nullable=False)
    new_weekday = db.Column(db.Integer, nullable=False)
    changed_on = db.Column(db.Date, nullable=False)
    previous_due_on = db.Column(db.Date, nullable=False)
