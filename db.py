import os
import csv
import io
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    """Get a database connection."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    # Contractors table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contractors (
            id SERIAL PRIMARY KEY,
            company_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT DEFAULT '',
            location TEXT DEFAULT '',
            plan TEXT DEFAULT 'launch',
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Leads table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
            contractor_id INTEGER REFERENCES contractors(id),
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

    # Add contractor_id column if it doesn't exist (migration for existing table)
    try:
        cur.execute("""
            ALTER TABLE leads ADD COLUMN IF NOT EXISTS contractor_id INTEGER REFERENCES contractors(id)
        """)
    except Exception:
        pass

    # Add Stripe fields to contractors
    try:
        cur.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT DEFAULT ''")
        cur.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT DEFAULT ''")
        cur.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS lead_limit INTEGER DEFAULT 100")
    except Exception:
        pass

    # Add voice branding to contractors
    try:
        cur.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS voice_company_name TEXT DEFAULT ''")
    except Exception:
        pass

    conn.commit()
    cur.close()
    conn.close()
    print("DATABASE: all tables ready")


# ==============================================================================
# CONTRACTOR AUTH
# ==============================================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_contractor(company_name: str, email: str, password: str, phone: str = "", location: str = "", plan: str = "launch") -> dict:
    """Create a new contractor account. Returns the contractor dict or None if email exists."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Check if email already exists
    cur.execute("SELECT id FROM contractors WHERE email = %s", (email.lower(),))
    if cur.fetchone():
        cur.close()
        conn.close()
        return None

    pw_hash = hash_password(password)
    cur.execute("""
        INSERT INTO contractors (company_name, email, password_hash, phone, location, plan)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, company_name, email, phone, location, plan, active, created_at
    """, (company_name, email.lower(), pw_hash, phone, location, plan))

    contractor = dict(cur.fetchone())
    conn.commit()
    cur.close()
    conn.close()
    return contractor


def authenticate_contractor(email: str, password: str) -> dict:
    """Authenticate a contractor. Returns contractor dict or None."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM contractors WHERE email = %s AND active = TRUE", (email.lower(),))
    contractor = cur.fetchone()
    cur.close()
    conn.close()

    if not contractor:
        return None

    contractor = dict(contractor)
    if not check_password(password, contractor["password_hash"]):
        return None

    # Don't return the hash
    del contractor["password_hash"]
    return contractor


def get_contractor_by_id(contractor_id: int) -> dict:
    """Get contractor by ID."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id, company_name, email, phone, location, plan, active, created_at FROM contractors WHERE id = %s",
        (contractor_id,)
    )
    contractor = cur.fetchone()
    cur.close()
    conn.close()
    return dict(contractor) if contractor else None


def get_all_contractors():
    """Get all contractors (admin only)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, company_name, email, phone, location, plan, active, created_at FROM contractors ORDER BY id DESC")
    contractors = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(c) for c in contractors]


# ==============================================================================
# LEADS — with contractor filtering
# ==============================================================================

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
    contractor_id: int = None,
):
    """Insert a new lead into the database."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO leads (
            contractor_id, name, phone, email, location, roof_type, issue, urgency,
            insurance_status, inspection_timing, message, lead_score,
            lead_temperature, assigned_contractor, assigned_email,
            assigned_phone, status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """, (
        contractor_id, name, phone, email, location, roof_type, issue, urgency,
        insurance_status, inspection_timing, message, lead_score,
        lead_temperature, assigned_contractor, assigned_email,
        assigned_phone, status,
    ))
    conn.commit()
    cur.close()
    conn.close()


def read_all_leads(contractor_id: int = None):
    """Read leads from the database. If contractor_id is provided, filter by contractor."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if contractor_id:
        cur.execute("SELECT * FROM leads WHERE contractor_id = %s ORDER BY id DESC", (contractor_id,))
    else:
        cur.execute("SELECT * FROM leads ORDER BY id DESC")
    leads = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in leads]


def update_lead_status(lead_id: int, status: str, contractor_id: int = None):
    """Update the status of a specific lead. If contractor_id provided, verify ownership."""
    conn = get_connection()
    cur = conn.cursor()
    if contractor_id:
        cur.execute("UPDATE leads SET status = %s WHERE id = %s AND contractor_id = %s", (status, lead_id, contractor_id))
    else:
        cur.execute("UPDATE leads SET status = %s WHERE id = %s", (status, lead_id))
    conn.commit()
    cur.close()
    conn.close()


def get_leads_by_status(status: str, contractor_id: int = None):
    """Get leads with a specific status, optionally filtered by contractor."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if contractor_id:
        cur.execute("SELECT * FROM leads WHERE status = %s AND contractor_id = %s ORDER BY id DESC", (status, contractor_id))
    else:
        cur.execute("SELECT * FROM leads WHERE status = %s ORDER BY id DESC", (status,))
    leads = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in leads]


def get_leads_by_location(location: str, contractor_id: int = None):
    """Get leads matching a location, optionally filtered by contractor."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if contractor_id:
        cur.execute(
            "SELECT * FROM leads WHERE LOWER(location) LIKE %s AND contractor_id = %s ORDER BY id DESC",
            (f"%{location.lower()}%", contractor_id)
        )
    else:
        cur.execute(
            "SELECT * FROM leads WHERE LOWER(location) LIKE %s ORDER BY id DESC",
            (f"%{location.lower()}%",)
        )
    leads = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in leads]


def export_leads_csv(contractor_id: int = None) -> str:
    """Export leads as a CSV string, optionally filtered by contractor."""
    leads = read_all_leads(contractor_id=contractor_id)
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
        lead["created_at"] = str(lead.get("created_at", ""))
        writer.writerow({k: lead.get(k, "") for k in fieldnames})

    return output.getvalue()


def get_lead_stats(contractor_id: int = None):
    """Get lead statistics, optionally filtered by contractor."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    where_clause = ""
    params = ()
    if contractor_id:
        where_clause = "WHERE contractor_id = %s"
        params = (contractor_id,)

    cur.execute(f"""
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
        {where_clause}
    """, params)
    stats = dict(cur.fetchone())
    cur.close()
    conn.close()
    return stats
def update_contractor_stripe(contractor_id: int, stripe_customer_id: str, stripe_subscription_id: str):
    """Link Stripe customer/subscription to a contractor."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE contractors SET stripe_customer_id = %s, stripe_subscription_id = %s WHERE id = %s",
        (stripe_customer_id, stripe_subscription_id, contractor_id)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_contractor_by_stripe_customer(stripe_customer_id: str) -> dict:
    """Find contractor by their Stripe customer ID."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id, company_name, email, phone, location, plan, lead_limit, stripe_customer_id, stripe_subscription_id FROM contractors WHERE stripe_customer_id = %s",
        (stripe_customer_id,)
    )
    contractor = cur.fetchone()
    cur.close()
    conn.close()
    return dict(contractor) if contractor else None


def get_monthly_lead_count(contractor_id: int) -> int:
    """Count leads for a contractor in the current calendar month."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM leads
        WHERE contractor_id = %s
        AND created_at >= date_trunc('month', NOW())
    """, (contractor_id,))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def update_contractor_plan(contractor_id: int, plan: str, lead_limit: int):
    """Update contractor's plan and lead limit."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE contractors SET plan = %s, lead_limit = %s WHERE id = %s",
        (plan, lead_limit, contractor_id)
    )
    conn.commit()
    cur.close()
    conn.close()