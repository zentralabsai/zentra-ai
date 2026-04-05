import os
import csv
import io
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    """Get a database connection."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create the leads table if it doesn't exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
            name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            location TEXT DEFAULT '',
            roof_type TEXT DEFAULT '',
            issue TEXT DEFAULT '',
            urgency TEXT DEFAULT '',
            insurance_status TEXT DEFAULT '',
            inspection_timing TEXT DEFAULT '',
            message TEXT DEFAULT '',
            lead_score INTEGER DEFAULT 0,
            lead_temperature TEXT DEFAULT 'COLD',
            assigned_contractor TEXT DEFAULT '',
            assigned_email TEXT DEFAULT '',
            assigned_phone TEXT DEFAULT '',
            status TEXT DEFAULT 'New',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("DATABASE: leads table ready")


def save_lead(
    name: str,
    phone: str,
    email: str,
    location: str,
    roof_type: str,
    issue: str,
    urgency: str,
    insurance_status: str,
    inspection_timing: str,
    lead_score: int,
    lead_temperature: str,
    assigned_contractor: str,
    assigned_email: str = "",
    assigned_phone: str = "",
    message: str = "",
    status: str = "New",
):
    """Insert a new lead into the database."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO leads (
            name, phone, email, location, roof_type, issue, urgency,
            insurance_status, inspection_timing, message, lead_score,
            lead_temperature, assigned_contractor, assigned_email,
            assigned_phone, status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """, (
        name, phone, email, location, roof_type, issue, urgency,
        insurance_status, inspection_timing, message, lead_score,
        lead_temperature, assigned_contractor, assigned_email,
        assigned_phone, status,
    ))
    conn.commit()
    cur.close()
    conn.close()


def read_all_leads():
    """Read all leads from the database, newest first."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM leads ORDER BY id DESC")
    leads = cur.fetchall()
    cur.close()
    conn.close()
    # Convert to list of plain dicts (for compatibility with existing code)
    return [dict(row) for row in leads]


def update_lead_status(lead_id: int, status: str):
    """Update the status of a specific lead by ID."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE leads SET status = %s WHERE id = %s", (status, lead_id))
    conn.commit()
    cur.close()
    conn.close()


def get_leads_by_status(status: str):
    """Get all leads with a specific status."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM leads WHERE status = %s ORDER BY id DESC", (status,))
    leads = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in leads]


def get_leads_by_location(location: str):
    """Get all leads matching a location (case-insensitive partial match)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM leads WHERE LOWER(location) LIKE %s ORDER BY id DESC",
        (f"%{location.lower()}%",)
    )
    leads = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in leads]


def export_leads_csv() -> str:
    """Export all leads as a CSV string."""
    leads = read_all_leads()
    if not leads:
        return ""

    output = io.StringIO()
    fieldnames = [
        "id", "name", "phone", "email", "location", "roof_type", "issue",
        "urgency", "insurance_status", "inspection_timing", "message",
        "lead_score", "lead_temperature", "assigned_contractor",
        "assigned_email", "assigned_phone", "status", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for lead in leads:
        # Convert datetime to string for CSV
        lead["created_at"] = str(lead.get("created_at", ""))
        writer.writerow({k: lead.get(k, "") for k in fieldnames})

    return output.getvalue()


def get_lead_stats():
    """Get lead statistics for the dashboard."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            COUNT(*) as total_leads,
            COUNT(*) FILTER (WHERE lead_temperature = 'HOT') as hot_leads,
            COUNT(*) FILTER (WHERE lead_temperature = 'WARM') as warm_leads,
            COUNT(*) FILTER (WHERE lead_temperature = 'COLD') as cold_leads,
            COUNT(*) FILTER (WHERE status = 'Contacted') as contacted_leads,
            COUNT(*) FILTER (WHERE status = 'Inspection Booked') as booked_leads,
            COUNT(*) FILTER (WHERE status = 'Won') as won_leads,
            COUNT(*) FILTER (WHERE status = 'Lost') as lost_leads
        FROM leads
    """)
    stats = dict(cur.fetchone())
    cur.close()
    conn.close()
    return stats