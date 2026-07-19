import os
from datetime import datetime, timedelta
from typing import Dict

import httpx
from sqlmodel import Session, select

from ironlog.integrations.withings_auth import refresh_access_token
from ironlog.models import WithingsCredentials, DailyReadiness


DEFAULT_LOOKBACK_HOURS = 48
WITHINGS_MEASURE_URL = "https://wbsapi.withings.net/measure"
KG_TO_LB = 2.20462


async def sync_withings_measurements(db: Session) -> dict:
    """Fetches new weight (type 1) and fat-ratio (type 6) measurements
    from Withings since WithingsCredentials.last_synced_at, upserts each
    date's DailyReadiness row, advances last_synced_at, returns a summary
    dict (e.g. {"days_updated": N, "measurements_fetched": N})."""
    creds = db.exec(select(WithingsCredentials).where(WithingsCredentials.id == 1)).one_or_none()
    if creds is None:
        raise RuntimeError("Withings not yet authorized — run /integrations/withings/authorize first")

    now = datetime.utcnow()
    
    # Refresh access token if expired
    if creds.token_expires_at <= now:
        client_id = os.environ.get("WITHINGS_CLIENT_ID", "")
        client_secret = os.environ.get("WITHINGS_CLIENT_SECRET", "")
        
        tokens = await refresh_access_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=creds.refresh_token,
        )
        
        creds.access_token = tokens["access_token"]
        creds.refresh_token = tokens["refresh_token"]
        creds.token_expires_at = now + timedelta(seconds=int(tokens["expires_in"]))
        creds.updated_at = now
        db.add(creds)
        # We can commit the token refresh early, or let it ride with the sync commit.
        # Let it ride with the sync commit.

    # Lookback window
    if creds.last_synced_at:
        startdate_ts = int(creds.last_synced_at.timestamp())
    else:
        startdate_ts = int((now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)).timestamp())

    # Fetch measurements
    data = {
        "action": "getmeas",
        "meastypes": "1,6",
        "category": 1,
        "startdate": startdate_ts,
    }
    headers = {
        "Authorization": f"Bearer {creds.access_token}"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(WITHINGS_MEASURE_URL, data=data, headers=headers)
    
    if response.status_code >= 400:
        raise RuntimeError(f"Withings measure request failed with HTTP {response.status_code}")
    
    payload = response.json()
    status = payload.get("status")
    if status != 0:
        error = payload.get("error", "Unknown error")
        raise RuntimeError(f"Withings measure request failed with status {status}: {error}")
    
    body = payload.get("body", {})
    measuregrps = body.get("measuregrps", [])

    # Group measurements by date
    daily_updates: Dict[datetime.date, dict] = {}
    total_measurements = 0

    for grp in measuregrps:
        grp_date = datetime.fromtimestamp(grp["date"]).date()
        if grp_date not in daily_updates:
            daily_updates[grp_date] = {}
            
        for measure in grp.get("measures", []):
            total_measurements += 1
            m_type = measure["type"]
            m_val = measure["value"] * (10 ** measure["unit"])
            
            if m_type == 1:
                # weight in kg -> lb
                daily_updates[grp_date]["bodyweight"] = m_val * KG_TO_LB
            elif m_type == 6:
                # fat ratio in %
                daily_updates[grp_date]["body_fat_pct"] = m_val

    # Upsert DailyReadiness
    for d, updates in daily_updates.items():
        if not updates:
            continue
            
        updates["bodyweight_source"] = "withings"
        
        row = db.exec(select(DailyReadiness).where(DailyReadiness.date == d)).first()
        if row is None:
            row = DailyReadiness(date=d, **updates)
            db.add(row)
        else:
            for key, value in updates.items():
                setattr(row, key, value)
            db.add(row)

    creds.last_synced_at = now
    creds.updated_at = now
    db.add(creds)
    db.commit()

    return {
        "days_updated": len(daily_updates),
        "measurements_fetched": total_measurements,
    }
