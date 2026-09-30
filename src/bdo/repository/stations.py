"""Station / system-node persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.models import Station
from bdo.schemas import NormalizedStation


def get(session: Session, source_id: int | None, external_station_id: str) -> Station | None:
    return session.scalar(
        select(Station).where(Station.source_id == source_id, Station.external_station_id == external_station_id)
    )


def upsert(session: Session, source_id: int, ns: NormalizedStation, raw_snapshot_id: int | None = None) -> Station:
    """Insert or update a node from normalised source data.

    Existing non-null coordinates are never overwritten by null; a coordinate change from the
    source replaces the old value (the previous raw snapshot remains the audit trail).
    """
    st = get(session, source_id, ns.external_station_id)
    if st is None:
        st = Station(source_id=source_id, external_station_id=ns.external_station_id, name=ns.name)
        session.add(st)
    st.name = ns.name
    st.node_type = ns.node_type.value
    if ns.latitude is not None:
        st.latitude, st.longitude = ns.latitude, ns.longitude
        st.coordinate_evidence_class = ns.coordinate_evidence_class
    st.district = ns.district or st.district
    st.operator = ns.operator or st.operator
    st.state_variable = ns.state_variable or st.state_variable
    st.validation_status = ns.validation_status
    st.source_type_code = ns.source_type_code or st.source_type_code
    st.notes = ns.notes or st.notes
    if raw_snapshot_id is not None:
        st.raw_snapshot_id = raw_snapshot_id
    session.flush()
    return st


def ensure_minimal(session: Session, source_id: int, external_station_id: str, name: str | None = None) -> Station:
    """Station referenced by a measurement but not described: create a bare UNVERIFIED node."""
    st = get(session, source_id, external_station_id)
    if st is None:
        st = Station(source_id=source_id, external_station_id=external_station_id,
                     name=name or external_station_id, node_type="other")
        session.add(st)
        session.flush()
    return st


def list_all(session: Session) -> list[Station]:
    return list(session.scalars(select(Station).order_by(Station.id)))
