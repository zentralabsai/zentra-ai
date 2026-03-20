from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from twilio.rest import Client
import os
import csv
import re
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LEADS_FILE = "leads.csv"

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
You are a high-converting AI roofing lead qualification assistant for a roofing company.

Your goal is to qualify the visitor and collect a roofing repair lead.

CORE RULES:
- Be warm, clear, confident, and conversational.
- Keep replies short and natural.
- Ask only ONE question at a time.
- Do NOT repeat questions that were already answered.
- Always acknowledge the user's last answer before moving on.
- Sound like a real roofing company assistant, not a generic chatbot.
- Do not give long educational explanations unless needed.

QUESTION ORDER:
1. What problem they are having with the roof
2. How urgent the issue is
3. Their city or zip code
4. The roof type if known (shingles, tile, metal, flat)
5. Whether they have already filed an insurance claim or want help checking
6. When they want an inspection (ASAP, this week, just researching)
7. Their name
8. Their phone number
9. Their email address

IMPORTANT BEHAVIOR:
- If the user mentions an active leak, emergency, storm damage, water coming in, ceiling damage, roof collapse risk, or anything urgent, acknowledge that it sounds urgent and say a roofing specialist should assess it quickly.
- If the user says they want help checking insurance, have not filed yet, or are unsure, acknowledge that and explain briefly that a roofing specialist can inspect the damage and help guide them through the insurance claim process.
- After acknowledging insurance help, continue to the next qualification question naturally.
- Keep the user moving toward booking an inspection.

INSURANCE RESPONSE STYLE:
If the user wants help checking insurance, respond in a style like:
"Got it — we can help with that. A roofing specialist can inspect the damage and help guide you through the insurance claim process."

URGENT RESPONSE STYLE:
If the issue sounds urgent, respond in a style like:
"That sounds urgent. Roof damage like this can get worse quickly, so let's get a few details so a roofing specialist can help as soon as possible."

LEAD COMPLETION:
After collecting their email address:
1. Briefly confirm the information.
2. Tell them a roofing specialist will call them shortly to schedule an inspection.
3. If insurance help was requested, mention that they can help guide them through the insurance claim process.
4. Encourage them to keep their phone nearby in case the contractor calls soon.

VERY IMPORTANT:
When the lead is complete, add this block at the very end of your message exactly like this:

LEAD_CAPTURED
Name: <name>
Phone: <phone>
Email: <email>
Location: <location>
Roof Type: <roof type>
Issue: <issue>
Urgency: <urgency>
Insurance Status: <insurance status>
Inspection Timing: <inspection timing>

Do not ask more questions after LEAD_CAPTURED.
MANDATORY CAPTURE RULE:
Do not output LEAD_CAPTURED unless you have all of the following:
- Name
- Phone
- Email
- Location
- Roof Type (if unknown, write "Unknown")
- Issue
- Urgency
- Insurance Status
- Inspection Timing

If phone is missing, ask for the phone number.
If email is missing, ask for the email address.
Never finalize the lead without phone and email.
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
            f"Hi {name}, thanks for contacting us. "
            f"We received your roofing request and a specialist will reach out shortly."
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

    return {"success": True, "index": index, "status": status}


# ---- YOUR LEADS DASHBOARD (ALREADY EXISTS BELOW) ----

@app.get("/leads", response_class=HTMLResponse)
def view_leads():
    leads = read_all_leads()
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
                <a class="btn" href="/">Back to Chat</a>
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


app.mount("/", StaticFiles(directory="static", html=True), name="static")