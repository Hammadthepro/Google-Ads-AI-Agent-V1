"""Remove leftover DEMO data (the old simulated world) from a database without touching real records.

What counts as demo, deliberately narrow:
  * every row of the mock_gads_* tables (the simulated Google Ads world),
  * clients linked to a simulated Google Ads customer, or whose business website is on the reserved `.example` TLD
    (no real site can use it) — together with everything that hangs off them through foreign keys,
  * demo users (`@napollo.example` addresses): DEACTIVATED, never deleted, because real history may reference them,
  * the retired `mock_clock` setting; the retired `mock` operating mode becomes `read_only`.

Dry-run by default: nothing changes unless apply=True. Real clients, accounts, credentials and settings are never matched.
"""
from __future__ import annotations

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import Base, Business, Client, GoogleAdsAccount, SystemSetting, User

MOCK_TABLES = ["mock_gads_call_records", "mock_gads_change_events", "mock_gads_search_terms", "mock_gads_metrics_daily",
               "mock_gads_resources", "mock_gads_customers", "mock_failure_injections"]
DEMO_EMAIL_SUFFIX = "@napollo.example"


def _mock_tables(db: Session) -> list[str]:
    existing = {t for (t,) in db.execute(text("select table_name from information_schema.tables where table_schema = current_schema()"))}
    return [t for t in MOCK_TABLES if t in existing]


def demo_client_ids(db: Session) -> set[str]:
    ids: set[str] = set()
    mock_customers: set[str] = set()
    if "mock_gads_customers" in _mock_tables(db):
        mock_customers = {c for (c,) in db.execute(text("select customer_id from mock_gads_customers"))}
    for acc in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id.is_not(None))):
        if acc.customer_id in mock_customers:
            ids.add(acc.client_id)
    for b in db.scalars(select(Business)):
        host = (b.website_url or "").split("//")[-1].split("/")[0].lower()
        if host.endswith(".example") or "(SIMULATED)" in (b.name or ""):
            ids.add(b.client_id)
    for c in db.scalars(select(Client)):
        if "(SIMULATED)" in (c.name or ""):
            ids.add(c.id)
    return ids


def demo_unlinked_accounts(db: Session) -> list[GoogleAdsAccount]:
    """Account rows that came from the simulated world but belong to no client (e.g. the simulated MCC)."""
    mock_customers: set[str] = set()
    if "mock_gads_customers" in _mock_tables(db):
        mock_customers = {c for (c,) in db.execute(text("select customer_id from mock_gads_customers"))}
    return [a for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id.is_(None)))
            if a.customer_id in mock_customers or "(SIMULATED)" in (a.descriptive_name or "") or a.integration_mode == "MOCK"]


def _doomed_rows(db: Session, client_ids: set[str]) -> dict[str, set]:
    """Every row reachable from the demo clients through foreign keys, as {table: {pk,...}}."""
    meta = Base.metadata
    doomed: dict[str, set] = {"clients": set(client_ids)}
    grew = True
    while grew:
        grew = False
        for table in meta.tables.values():
            pk_cols = list(table.primary_key.columns)
            if len(pk_cols) != 1:
                continue
            for fk in table.foreign_keys:
                parent = fk.column.table.name
                if parent not in doomed or not doomed[parent] or fk.column.name != list(fk.column.table.primary_key.columns)[0].name:
                    continue
                rows = db.execute(select(pk_cols[0]).where(fk.parent.in_(doomed[parent]))).scalars().all()
                new = set(rows) - doomed.setdefault(table.name, set())
                if new:
                    doomed[table.name] |= new
                    grew = True
    return doomed


def purge(db: Session, *, apply: bool = False) -> dict:
    report: dict = {"applied": apply, "mock_tables": {}, "demo_clients": [], "rows_by_table": {}, "users_deactivated": [], "settings": []}
    for t in _mock_tables(db):
        report["mock_tables"][t] = db.execute(text(f'select count(*) from "{t}"')).scalar_one()
    cids = demo_client_ids(db)
    orphan_accounts = demo_unlinked_accounts(db)
    report["unlinked_demo_accounts"] = [a.customer_id for a in orphan_accounts]
    report["demo_clients"] = [{"id": c.id, "name": c.name} for c in db.scalars(select(Client).where(Client.id.in_(cids or {"-"})))]
    doomed = _doomed_rows(db, cids) if cids else {}
    report["rows_by_table"] = {t: len(v) for t, v in doomed.items() if v}
    demo_users = list(db.scalars(select(User).where(User.email.like(f"%{DEMO_EMAIL_SUFFIX}"), User.is_active.is_(True))))
    report["users_deactivated"] = [u.email for u in demo_users]
    if db.get(SystemSetting, "mock_clock"):
        report["settings"].append("mock_clock removed")
    mode = db.get(SystemSetting, "operating_mode")
    if mode and "mock" in str(mode.value):
        report["settings"].append("operating_mode mock -> read_only")
    if not apply:
        return report
    order = [t for t in reversed(Base.metadata.sorted_tables) if t.name in doomed and doomed[t.name]]
    pk_of = {t.name: list(t.primary_key.columns)[0] for t in order}
    for t in order:
        db.execute(delete(t).where(pk_of[t.name].in_(doomed[t.name])))
    for a in orphan_accounts:
        db.delete(a)
    for t in _mock_tables(db):
        db.execute(text(f'delete from "{t}"'))
    for u in demo_users:
        u.is_active = False
    row = db.get(SystemSetting, "mock_clock")
    if row:
        db.delete(row)
    if mode and "mock" in str(mode.value):
        mode.value = "read_only"
    db.flush()
    return report
