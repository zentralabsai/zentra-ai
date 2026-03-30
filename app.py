from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from twilio.rest import Client
import os
import csv
import re
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import uuid
import requests
import stripe
load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

def send_email(to_email, subject, body, from_email="notifications@kazfen.com"):
    requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": f"Kazfen <{from_email}>",
            "to": [to_email],
            "subject": subject,
            "html": body,
        },
    )


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()
WEBCHAT_SESSIONS = {}
SMS_SESSIONS = {}
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
def home():
    return FileResponse("static/index.html")
@app.get("/test-email")
def test_email():
    send_email(
        "yourpersonalemail@gmail.com",
        "Kazfen Email Test",
        "If you receive this, SMTP is working."
    )
    return {"status": "email sent"}
from fastapi import Request

@app.post("/api/lead")
async def receive_lead(request: Request):
    data = await request.json()

    name = data.get("name", "")
    phone = data.get("phone", "")
    email = data.get("email", "")
    location = data.get("location", "")
    service = data.get("service", "")
    roof_type = data.get("roof_type", "")
    active_leak = data.get("active_leak", "")
    insurance_claim = data.get("insurance_claim", "")
    budget = data.get("budget", "")
    preferred_inspection_time = data.get("preferred_inspection_time", "")
    message = data.get("message", "")
    urgency = data.get("urgency", "")

    issue = service
    insurance_status = insurance_claim
    inspection_timing = preferred_inspection_time if preferred_inspection_time else "Not specified"

    if str(urgency).lower() in ["high", "very urgent", "urgent", "asap"]:
        lead_score = 9
        lead_temperature = "HOT"
    elif str(urgency).lower() in ["medium", "soon"]:
        lead_score = 6
        lead_temperature = "WARM"
    else:
        lead_score = 3
        lead_temperature = "COLD"

    assigned_contractor = "Default Contractor"
    status = "New"

    with open("leads.csv", "a") as f:
        f.write(
        f"{name},{phone},{email},{location},{roof_type},{issue},{urgency},"
        f"{insurance_status},{inspection_timing},{message},{lead_score},{lead_temperature},"
        f"{assigned_contractor},{status}\n"
    )

    return {"message": "Lead submitted successfully"}

LEADS_FILE = "leads.csv"
BOOKING_LINK = "https://calendly.com/bookings-kazfen/30min"
CONTRACTOR_ROUTES = {
    "miami": {
        "email": os.getenv("CONTRACTOR_MIAMI_EMAIL"),
        "phone": os.getenv("CONTRACTOR_MIAMI_PHONE"),
        "label": "Miami Contractor",
    },
    "new york": {
        "email": os.getenv("CONTRACTOR_NYC_EMAIL"),
        "phone": os.getenv("CONTRACTOR_NYC_PHONE"),
        "label": "NYC Contractor",
    },
    "nyc": {
        "email": os.getenv("CONTRACTOR_NYC_EMAIL"),
        "phone": os.getenv("CONTRACTOR_NYC_PHONE"),
        "label": "NYC Contractor",
    },
    "los angeles": {
        "email": os.getenv("CONTRACTOR_LA_EMAIL"),
        "phone": os.getenv("CONTRACTOR_LA_PHONE"),
        "label": "LA Contractor",
    },
    "la": {
        "email": os.getenv("CONTRACTOR_LA_EMAIL"),
        "phone": os.getenv("CONTRACTOR_LA_PHONE"),
        "label": "LA Contractor",
    },
}


def get_contractor_for_location(location: str) -> dict:
    location_lower = (location or "").lower()

    for key, contractor in CONTRACTOR_ROUTES.items():
        if key in location_lower:
            return contractor

    return {
        "email": os.getenv("CONTRACTOR_DEFAULT_EMAIL"),
        "phone": os.getenv("CONTRACTOR_DEFAULT_PHONE"),
        "label": "Default Contractor",
    }

SYSTEM_PROMPT = """
You are Kazfen, a high-converting AI roofing lead qualification assistant for a roofing company.
Your job is to qualify inbound roofing leads and collect the right information for the sales team.

GOALS:
- Classify the lead into one or more of these:
  - Emergency leak
  - Repair
  - Full replacement
  - Storm damage
  - Insurance claim
  - Inspection
  - Commercial roofing
  - Residential roofing

- Collect these fields naturally in conversation:
  - Name
  - Phone
  - Email
  - Address or city/zip
  - Roof type
  - Age of roof
  - Leak? (Yes/No)
  - Storm damage? (Yes/No)
  - Insurance claim? (Yes/No)
  - Urgency
  - Timeline
  - Budget (optional)

CORE RULES:
- Be warm, clear, concise, and professional.
- Sound like a real roofing intake specialist, not a generic chatbot.
- Ask only ONE question at a time.
- Do not repeat questions already answered.
- Keep the user moving toward qualification and inspection booking.
- If the issue sounds urgent, acknowledge urgency and move faster.

CLASSIFICATION LOGIC:
- If the user mentions water coming in, active leaking, interior damage, or emergency, classify as Emergency leak.
- If the user mentions patching, fixing a small issue, or minor damage, classify as Repair.
- If the user mentions old roof, full roof, reroofing, or replacement, classify as Full replacement.
- If the user mentions storm, hail, wind, tree damage, or weather event, classify as Storm damage.
- If the user mentions claim, insurer, adjuster, or coverage, classify as Insurance claim.
- If the user wants someone to inspect, quote, or check damage, classify as Inspection.
- If the property is a business, warehouse, office, retail unit, apartment complex, or commercial building, classify as Commercial roofing.
- Otherwise default to Residential roofing unless clearly commercial.

QUESTION ORDER:
1. What roofing issue are you dealing with?
2. Is there an active leak or water coming in?
3. Was this caused by storm or weather damage?
4. Have you filed an insurance claim or do you want help checking insurance?
5. What type of property is this: residential or commercial?
6. What type of roof is it, if you know? (shingle, tile, metal, flat, etc.)
7. Roughly how old is the roof?
8. What is the property address or city/zip?
9. How urgent is this?
10. What timeline are you aiming for?
11. What is your name?
12. What is your phone number?
13. What is your email?
14. Optional: do you have a rough budget in mind?

URGENT RESPONSE STYLE:
If the user sounds urgent, respond like:
"That sounds urgent. Let’s get a few quick details so a roofing specialist can follow up fast."

INSURANCE RESPONSE STYLE:
If the user mentions insurance, respond like:
"Got it — we can help with that. A roofing specialist can inspect the damage and guide you through the insurance side."

VERY IMPORTANT:
When the lead is fully captured, end your final message with this exact block format:

LEAD CAPTURED
Name: <name>
Phone: <phone>
Email: <email>
Address: <address>
Lead Type: <lead type>
Property Type: <commercial or residential>
Roof Type: <roof type>
Roof Age: <roof age>
Leak: <yes/no>
Storm Damage: <yes/no>
Insurance Claim: <yes/no>
Urgency: <urgency>
Timeline: <timeline>
Budget: <budget>
Notes: <summary>
"""


def score_lead(
    issue: str,
    urgency: str,
    insurance_status: str,
    inspection_timing: str,
    location: str,
    roof_type: str,
):
    score = 0

    issue_lower = (issue or "").lower()
    urgency_lower = (urgency or "").lower()
    insurance_lower = (insurance_status or "").lower()
    inspection_lower = (inspection_timing or "").lower()
    location_lower = (location or "").lower()
    roof_type_lower = (roof_type or "").lower()

    if any(word in urgency_lower for word in ["emergency", "urgent", "very", "asap", "immediate"]):
        score += 3

    if "asap" in inspection_lower:
        score += 3
    elif "this week" in inspection_lower or "tomorrow" in inspection_lower:
        score += 2
    
    # High urgency issues
    if any(word in issue_lower for word in ["active leak", "water coming in", "severe", "collapse"]):
        score += 3

    # Medium issues
    elif any(word in issue_lower for word in ["leak", "storm", "damage", "missing shingles"]):
        score += 1

    if any(word in insurance_lower for word in ["help", "checking", "claim", "not filed", "insurance"]):
        score += 2

    if roof_type_lower.strip():
        score += 1

    if location_lower.strip():
        score += 1

    if score >= 8:
        temperature = "HOT"
    elif score >= 5:
        temperature = "WARM"
    else:
        temperature = "COLD"

    return score, temperature


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
    assigned_email: str,
    assigned_phone: str,
):
    file_exists = os.path.exists(LEADS_FILE)

    with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "name",
                "phone",
                "email",
                "location",
                "roof_type",
                "issue",
                "urgency",
                "insurance_status",
                "inspection_timing",
                "lead_score",
                "lead_temperature",
                "assigned_contractor",
                "assigned_email",
                "assigned_phone",
            ])

        writer.writerow([
            name,
            phone,
            email,
            location,
            roof_type,
            issue,
            urgency,
            insurance_status,
            inspection_timing,
            lead_score,
            lead_temperature,
            assigned_contractor,
            assigned_email,
            assigned_phone,
        ])
        


def extract_field(text: str, field_name: str) -> str:
    pattern = rf"{re.escape(field_name)}:\s*(.+)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def looks_urgent(text: str) -> bool:
    text = text.lower()
    urgent_keywords = [
        "emergency",
        "urgent",
        "active leak",
        "leaking badly",
        "water coming in",
        "ceiling damage",
        "storm damage",
        "roof collapse",
        "severe leak",
        "asap",
        "leak",
        "roof leaking",
        "water damage",
    ]
    return any(keyword in text for keyword in urgent_keywords)


def wants_insurance_help(text: str) -> bool:
    text = text.lower()
    insurance_keywords = [
        "help checking",
        "help with insurance",
        "check insurance",
        "not filed",
        "haven't filed",
        "have not filed",
        "unsure about insurance",
        "need help checking",
        "want help checking",
        "yes i want help checking",
        "help checking on that",
        "insurance help",
    ]
    return any(keyword in text for keyword in insurance_keywords)


def send_email_notification(
    contractor_email: str,
    contractor_label: str,
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
):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_PASSWORD")
    notify_email = contractor_email

    if not smtp_server or not smtp_email or not smtp_password or not notify_email:
        print("Email skipped: missing SMTP env vars")
        return

    body = f"""New Roofing Lead

Assigned Contractor: {contractor_label}

Name: {name}
Phone: {phone}
Email: {email}
Location: {location}
Roof Type: {roof_type}
Issue: {issue}
Urgency: {urgency}
Insurance Status: {insurance_status}
Inspection Timing: {inspection_timing}
Lead Score: {lead_score}
Lead Temperature: {lead_temperature}
"""

    msg = MIMEText(body)
    msg["Subject"] = f"New Roofing Lead - {lead_temperature}"
    msg["From"] = smtp_email
    msg["To"] = notify_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
        print("Email sent to:", notify_email)
    except Exception as e:
        print("EMAIL ERROR:", str(e))


def send_sms_notification(
    contractor_phone: str,
    contractor_label: str,
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
):
    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")
        print("DEBUG contractor_label:", contractor_label)
        print("DEBUG contractor_phone:", contractor_phone)
        print("TWILIO_SID exists:", bool(twilio_sid))
        print("TWILIO_AUTH exists:", bool(twilio_auth))
        print("TWILIO_NUMBER:", twilio_number)
        print("CONTRACTOR_PHONE:", contractor_phone)

        if not twilio_sid or not twilio_auth or not twilio_number or not contractor_phone:
            print("SMS skipped: missing Twilio env vars")
            return

        twilio_client = Client(twilio_sid, twilio_auth)

        sms_body = f"""🔥 New Roofing Lead

Assigned: {contractor_label}
Name: {name}
Phone: {phone}
Email: {email}
Location: {location}
Roof: {roof_type}
Issue: {issue}
Urgency: {urgency}
Insurance: {insurance_status}
Timing: {inspection_timing}
Score: {lead_score} ({lead_temperature})"""

        message = twilio_client.messages.create(
            body=sms_body,
            from_=twilio_number,
            to=contractor_phone,
        )
        print("SMS sent:", message.sid)

    except Exception as e:
        print("SMS ERROR:", str(e))


def send_customer_confirmation_sms(name: str, customer_phone: str):
    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")

        if not twilio_sid or not twilio_auth or not twilio_number or not customer_phone:
            print("Customer SMS skipped: missing env vars or phone")
            return

        twilio_client = Client(twilio_sid, twilio_auth)

        sms_body = (
    f"Hi {name}, thanks for contacting Kazfen. "
    f"Book your demo here: {BOOKING_LINK}"
)




        message = twilio_client.messages.create(
            body=sms_body,
            from_=twilio_number,
            to=customer_phone,
        )

        print("Customer confirmation SMS sent:", message.sid)

    except Exception as e:
        print("CUSTOMER SMS ERROR:", str(e))


@app.get("/ask")
def ask_ai(question: str, history: str = ""):
    question_lower = question.lower()

    insurance_context = ""
    urgency_context = ""

    if wants_insurance_help(question_lower):
        insurance_context = (
            "The user wants help checking insurance. "
            "Acknowledge that clearly and explain briefly that a roofing specialist "
            "can inspect the damage and help guide them through the insurance claim process "
            "before asking the next question."
        )

    if looks_urgent(question_lower):
        urgency_context = (
            "The user's issue sounds urgent. "
            "Acknowledge urgency clearly and say roof damage can worsen quickly, "
            "then continue qualification."
        )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"""
Conversation so far:
{history}

Latest user message:
{question}

Extra guidance:
{insurance_context}
{urgency_context}
""",
            },
        ],
    )

    answer = response.choices[0].message.content or ""

    if "LEAD_CAPTURED" in answer:
        answer += f"\n\nBook your demo here: {BOOKING_LINK}"
        name = extract_field(answer, "Name")
        phone = extract_field(answer, "Phone")
        email = extract_field(answer, "Email")
        location = extract_field(answer, "Location")
        roof_type = extract_field(answer, "Roof Type")
        issue = extract_field(answer, "Issue")
        urgency = extract_field(answer, "Urgency")
        insurance_status = extract_field(answer, "Insurance Status")
        inspection_timing = extract_field(answer, "Inspection Timing")

        lead_score, lead_temperature = score_lead(
            issue=issue,
            urgency=urgency,
            insurance_status=insurance_status,
            inspection_timing=inspection_timing,
            location=location,
            roof_type=roof_type,
        )

        contractor = get_contractor_for_location(location)
        contractor_email = contractor["email"]
        contractor_phone = contractor["phone"]
        contractor_label = contractor["label"]
        
        save_lead(
    name,
    phone,
    email,
    location,
    roof_type,
    issue,
    urgency,
    insurance_status,
    inspection_timing,
    lead_score,
    lead_temperature,
    contractor_label,
    contractor_email,
    contractor_phone,
)

        if lead_temperature in ["HOT", "WARM"]:
            
            send_email_notification(
    contractor_email,
    contractor_label,
    name,
    phone,
    email,
    location,
    roof_type,
    issue,
    urgency,
    insurance_status,
    inspection_timing,
    lead_score,
    lead_temperature,
)

        send_sms_notification(
            contractor_phone,
            contractor_label,
            name,
            phone,
            email,
            location,
            roof_type,
            issue,
            urgency,
            insurance_status,
            inspection_timing,
            lead_score,
            lead_temperature,
        )

        send_customer_confirmation_sms(name, phone)

    return {"ai_response": answer}

def generate_inbound_reply(user_message: str, channel: str = "webchat") -> str:
    """
    Handles inbound messages from webchat, SMS, and calls
    """
    result = ask_ai(user_message)
    return result["ai_response"]

def read_all_leads():
    if not os.path.exists(LEADS_FILE):
        return []

    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_all_leads(leads):
    fieldnames = [
        "name",
        "phone",
        "email",
        "location",
        "roof_type",
        "issue",
        "urgency",
        "insurance_status",
        "inspection_timing",
        "lead_score",
        "lead_temperature",
        "assigned_contractor",
        "assigned_email",
        "assigned_phone",
        "status",
    ]

    with open(LEADS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leads)


# ---- STATUS UPDATE ROUTE (PASTE HERE) ----
@app.get("/update-status")
def update_status(index: int, status: str):
    allowed_statuses = {"New", "Contacted", "Inspection Booked", "Won", "Lost"}

    if status not in allowed_statuses:
        return {"error": "Invalid status"}

    leads = read_all_leads()

    if index < 0 or index >= len(leads):
        return {"error": "Invalid lead index"}

    leads[index]["status"] = status
    write_all_leads(leads)

    from fastapi.responses import RedirectResponse

# ---- YOUR LEADS DASHBOARD (ALREADY EXISTS BELOW) ----

@app.get("/export-leads")
def export_leads():
    return FileResponse(
        LEADS_FILE,
        media_type="text/csv",
        filename="kazfen-leads.csv"
    )

@app.get("/leads", response_class=HTMLResponse)
def view_leads():
    leads = read_all_leads()
    total_leads = len(leads)
    hot_leads = sum(1 for lead in leads if lead.get("lead_temperature", "") == "HOT")
    warm_leads = sum(1 for lead in leads if lead.get("lead_temperature", "") == "WARM")
    cold_leads = sum(1 for lead in leads if lead.get("lead_temperature", "") == "COLD")

    contacted_leads = sum(1 for lead in leads if lead.get("status", "New") == "Contacted")
    booked_leads = sum(1 for lead in leads if lead.get("status", "New") == "Inspection Booked")
    won_leads = sum(1 for lead in leads if lead.get("status", "New") == "Won")
    lost_leads = sum(1 for lead in leads if lead.get("status", "New") == "Lost")
    avg_job_value = 8000
    close_rate = 0.30
    pipeline_value = total_leads * avg_job_value
    expected_revenue = int(pipeline_value * close_rate)
    leads.reverse()

    rows = ""
    for reversed_index, lead in enumerate(leads):
        original_index = len(leads) - 1 - reversed_index
        temperature = lead.get("lead_temperature", "")
        score = lead.get("lead_score", "")
        status = lead.get("status", "New")

        badge_color = "#16a34a"
        if temperature == "WARM":
            badge_color = "#f59e0b"
        elif temperature == "COLD":
            badge_color = "#6b7280"

        status_color = "#2563eb"
        if status == "Contacted":
            status_color = "#7c3aed"
        elif status == "Inspection Booked":
            status_color = "#0f766e"
        elif status == "Won":
            status_color = "#16a34a"
        elif status == "Lost":
            status_color = "#dc2626"

        rows += f"""
        <tr>
            <td>{lead.get("name", "")}</td>
            <td>{lead.get("phone", "")}</td>
            <td>{lead.get("email", "")}</td>
            <td>{lead.get("location", "")}</td>
            <td>{lead.get("roof_type", "")}</td>
            <td>{lead.get("issue", "")}</td>
            <td>{lead.get("urgency", "")}</td>
            <td>{lead.get("insurance_status", "")}</td>
            <td>{lead.get("inspection_timing", "")}</td>
            <td>{lead.get("message", "")}</td>
            <td>{lead.get("assigned_contractor", "")}</td>
            <td>{score}</td>
            <td>
                <span style="
                    background:{badge_color};
                    color:white;
                    padding:4px 10px;
                    border-radius:999px;
                    font-size:12px;
                    font-weight:700;
                ">
                    {temperature}
                </span>
            </td>
            <td>
                <span style="
                    background:{status_color};
                    color:white;
                    padding:4px 10px;
                    border-radius:999px;
                    font-size:12px;
                    font-weight:700;
                ">
                    {status}
                </span>
            </td>
            <td>
                <div style="display:flex; flex-wrap:wrap; gap:6px;">
                    <a href="/update-status?index={original_index}&status=New" class="mini-btn">New</a>
                    <a href="/update-status?index={original_index}&status=Contacted" class="mini-btn">Contacted</a>
                    <a href="/update-status?index={original_index}&status=Inspection%20Booked" class="mini-btn">Booked</a>
                    <a href="/update-status?index={original_index}&status=Won" class="mini-btn">Won</a>
                    <a href="/update-status?index={original_index}&status=Lost" class="mini-btn">Lost</a>
                </div>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="14" style="text-align:center; padding:30px; color:#666;">
                No leads captured yet.
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Leads Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                margin: 0;
                padding: 30px;
                color: #111827;
            }}
            .wrap {{
                max-width: 1600px;
                margin: 0 auto;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .title {{
                font-size: 28px;
                font-weight: 800;
            }}
            .sub {{
                color: #6b7280;
                margin-top: 6px;
                font-size: 14px;
            }}
            .card {{
                background: white;
                border-radius: 16px;
                box-shadow: 0 8px 30px rgba(0,0,0,0.08);
                overflow: hidden;
            }}

                        .kpi-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 16px;
                margin-bottom: 20px;
            }}
            .kpi-card {{
                background: white;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 8px 30px rgba(0,0,0,0.08);
            }}
            .kpi-label {{
                color: #6b7280;
                font-size: 13px;
                font-weight: 700;
                margin-bottom: 8px;
                text-transform: uppercase;
                letter-spacing: 0.03em;
            }}
            .kpi-value {{
                font-size: 28px;
                font-weight: 800;
                color: #111827;
            }}

            @media (max-width: 900px) {{
    .kpi-grid {{
        grid-template-columns: 1fr 1fr;
    }}
}}

@media (max-width: 600px) {{
    .kpi-grid {{
        grid-template-columns: 1fr;
    }}
}}

            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }}
            th {{
                background: #111827;
                color: white;
                text-align: left;
                padding: 14px;
                position: sticky;
                top: 0;
            }}
            td {{
                padding: 14px;
                border-bottom: 1px solid #e5e7eb;
                vertical-align: top;
            }}
            tr:hover {{
                background: #f9fafb;
            }}
            .table-wrap {{
                overflow-x: auto;
            }}
            .btn {{
                display: inline-block;
                text-decoration: none;
                background: #2563eb;
                color: white;
                padding: 10px 16px;
                border-radius: 10px;
                font-weight: 700;
            }}
            .mini-btn {{
                display: inline-block;
                text-decoration: none;
                background: #e5e7eb;
                color: #111827;
                padding: 6px 10px;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            .mini-btn:hover {{
                background: #d1d5db;
            }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="header">
                <div>
                    <div class="title">Leads Dashboard</div>
                    <div class="sub">View and manage captured roofing leads</div>
                </div>
                <div style="display:flex; gap:10px;">
    <a class="btn" href="/export-leads">Export CSV</a>
    <a class="btn" href="/">Back to Chat</a>
</div>
            </div>
            <div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">Total Leads</div><div class="kpi-value">{total_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Hot Leads</div><div class="kpi-value">{hot_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Warm Leads</div><div class="kpi-value">{warm_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Cold Leads</div><div class="kpi-value">{cold_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Contacted</div><div class="kpi-value">{contacted_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Booked</div><div class="kpi-value">{booked_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Won</div><div class="kpi-value">{won_leads}</div></div>
    <div class="kpi-card"><div class="kpi-label">Lost</div><div class="kpi-value">{lost_leads}</div></div>
    <div class="kpi-card">
    <div class="kpi-label">Avg Job Value</div>
    <div class="kpi-value">${avg_job_value:,}</div>
</div>

<div class="kpi-card">
    <div class="kpi-label">Close Rate</div>
    <div class="kpi-value">{int(close_rate * 100)}%</div>
</div>

<div class="kpi-card">
    <div class="kpi-label">Pipeline Value</div>
    <div class="kpi-value">${pipeline_value:,}</div>
</div>

<div class="kpi-card">
    <div class="kpi-label">Expected Revenue</div>
    <div class="kpi-value">${expected_revenue:,}</div>
</div>
    </div>
            <div class="card">
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Phone</th>
                                <th>Email</th>
                                <th>Location</th>
                                <th>Roof Type</th>
                                <th>Issue</th>
                                <th>Urgency</th>
                                <th>Insurance</th>
                                <th>Inspection Timing</th>
                                <th>Message</th>
                                <th>Assigned Contractor</th>
                                <th>Score</th>
                                <th>Temperature</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

@app.post("/api/chat/start")
async def start_chat():
    session_id = os.urandom(8).hex()
    WEBCHAT_SESSIONS[session_id] = []
    return {
        "session_id": session_id,
        "message": "Hi — what roofing issue are you dealing with today?"
    }


@app.post("/api/chat/message")
async def chat_message(request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    message = data.get("message", "").strip()

    if not session_id or session_id not in WEBCHAT_SESSIONS:
        return {"error": "Invalid session"}

    if not message:
        return {"error": "Message required"}

    WEBCHAT_SESSIONS[session_id].append(message)

    ai_reply = generate_inbound_reply(message, channel="webchat")

    WEBCHAT_SESSIONS[session_id].append(ai_reply)

    return {"reply": ai_reply}


@app.post("/twilio/sms")
async def twilio_sms(request: Request):
    form = await request.form()
    from_number = str(form.get("From", "")).strip()
    body = str(form.get("Body", "")).strip()

    if from_number not in SMS_SESSIONS:
        SMS_SESSIONS[from_number] = []

    SMS_SESSIONS[from_number].append(body)

    ai_reply = generate_inbound_reply(body, channel="sms")

    SMS_SESSIONS[from_number].append(ai_reply)

    twiml = MessagingResponse()
    twiml.message(ai_reply)

    return PlainTextResponse(str(twiml), media_type="application/xml")


@app.post("/twilio/voice")
async def twilio_voice():
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/twilio/voice/process",
        method="POST",
        speech_timeout="auto"
    )
    gather.say(
        "Thanks for calling. Please briefly tell us what roofing issue you are dealing with.",
        voice="alice"
    )
    response.append(gather)

    response.say("We did not receive your message. Please call again.", voice="alice")
    return PlainTextResponse(str(response), media_type="application/xml")


@app.post("/twilio/voice/process")
async def twilio_voice_process(request: Request):
    form = await request.form()
    speech_result = str(form.get("SpeechResult", "")).strip()

    ai_reply = generate_inbound_reply(
        speech_result or "Caller did not provide details.",
        channel="phone"
    )

    response = VoiceResponse()
    response.say(ai_reply, voice="alice")
    response.say("A roofing specialist will follow up shortly. Goodbye.", voice="alice")

    return PlainTextResponse(str(response), media_type="application/xml")

@app.post("/twilio/sms")
async def twilio_sms(request: Request):
    form = await request.form()
    from_number = str(form.get("From", "")).strip()
    body = str(form.get("Body", "")).strip()

    if from_number not in SMS_SESSIONS:
        SMS_SESSIONS[from_number] = []

    SMS_SESSIONS[from_number].append(body)

    ai_reply = generate_inbound_reply(body, channel="sms")

    SMS_SESSIONS[from_number].append(ai_reply)

    twiml = MessagingResponse()
    twiml.message(ai_reply)

    return PlainTextResponse(str(twiml), media_type="application/xml")

@app.post("/twilio/voice")
async def twilio_voice():
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/twilio/voice/process",
        method="POST",
        speech_timeout="auto"
    )
    gather.say(
        "Thanks for calling. Please briefly tell us what roofing issue you are dealing with.",
        voice="alice"
    )
    response.append(gather)

    response.say("We did not receive your message. Please call again.", voice="alice")
    return PlainTextResponse(str(response), media_type="application/xml")


@app.post("/twilio/voice/process")
async def twilio_voice_process(request: Request):
    form = await request.form()
    speech_result = str(form.get("SpeechResult", "")).strip()

    ai_reply = generate_inbound_reply(
        speech_result or "Caller did not provide details.",
        channel="phone"
    )

    response = VoiceResponse()
    response.say(ai_reply, voice="alice")
    response.say("A roofing specialist will follow up shortly. Goodbye.", voice="alice")

    return PlainTextResponse(str(response), media_type="application/xml")


from fastapi.responses import RedirectResponse

@app.get("/create-checkout-launch")
def create_checkout_launch():
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price": "price_1TG32LCNb2u2ZxIIx4YaMYLG",  # Launch monthly €299
                "quantity": 1,
            },
            {
                "price": "price_1TGGc6CNb2u2ZxIIMeH8wBo5",  # Launch setup fee
                "quantity": 1,
            },
        ],
        success_url="https://kazfen.com/static/success.html",
        cancel_url="https://kazfen.com/static/pricing.html",
    )
    return RedirectResponse(session.url)


@app.get("/create-checkout-growth")
def create_checkout_growth():
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price": "price_1TG345CNb2u2ZxII6Z8sx8A9",  # Growth monthly €999
                "quantity": 1,
            },
            {
                "price": "price_1TGGcfCNb2u2ZxIIjcmZF9xT",  # Growth setup fee
                "quantity": 1,
            },
        ],
        success_url="https://kazfen.com/static/success.html",
        cancel_url="https://kazfen.com/static/pricing.html",
    )
    return RedirectResponse(session.url)


STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        print("Webhook error:", e)
        return {"status": "error"}

    print("Stripe event:", event["type"])

    if event["type"] == "payment_intent.succeeded":
        print("Payment succeeded")

    elif event["type"] == "invoice.paid":
        print("Subscription payment successful")

    elif event["type"] == "invoice.payment_failed":
        print("Payment failed")

    return {"status": "success"}

app.mount("/", StaticFiles(directory="static", html=True), name="static")


