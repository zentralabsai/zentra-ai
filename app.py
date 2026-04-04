from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from twilio.rest import Client
import os
import csv
import re
import time
import uuid
import requests
import stripe
import smtplib
from dotenv import load_dotenv
from email.mime.text import MIMEText
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from fastapi import Form
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

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
VOICE_SESSIONS = {}
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from starlette.middleware.sessions import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    secret_key="n53M+r9gA+B6Xrarun0p5w==",
    same_site="lax",
    https_only=True,
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

    weather_data = {}
    storm_boost = 0
    hail_boost = 0
    if location:
        weather_data = get_weather_for_location(location)
        storm_boost = get_storm_score_boost(weather_data)
        hail_data = get_nws_alerts_for_location(location)
        hail_boost = get_hail_score_boost(hail_data)
    if str(urgency).lower() in ["high", "very urgent", "urgent", "asap"]:
        lead_score = 9 + storm_boost + hail_boost
        lead_temperature = "HOT"
    elif str(urgency).lower() in ["medium", "soon"]:
        lead_score = 6 + storm_boost + hail_boost
        lead_temperature = "HOT" if (6 + storm_boost + hail_boost) >= 8 else "WARM"
    else:
        lead_score = 3 + storm_boost + hail_boost
        if (3 + storm_boost + hail_boost) >= 8:
            lead_temperature = "HOT"
        elif (3 + storm_boost + hail_boost) >= 5:
            lead_temperature = "WARM"
        else:
            lead_temperature = "COLD"

            # AI Qualification — overrides basic scoring if available
    ai_result = ai_qualify_lead(
        name=name, phone=phone, email=email, location=location,
        roof_type=roof_type, issue=issue, urgency=urgency,
        insurance_status=insurance_status, inspection_timing=inspection_timing,
        message=message, weather_data=weather_data, hail_data=hail_data,
    )
    if ai_result:
        lead_score = ai_result["score"]
        lead_temperature = ai_result["temperature"]

    assigned_contractor = "Default Contractor"
    status = "New"

    with open("leads.csv", "a") as f:
        f.write(
        f"{name},{phone},{email},{location},{roof_type},{issue},{urgency},"
        f"{insurance_status},{inspection_timing},{message},{lead_score},{lead_temperature},"
        f"{assigned_contractor},{status}\n"
    )
    
    try:
        if phone:
            storm_info = ""
            if weather_data.get("has_storm"):
                storm_info = f" ACTIVE STORM: {', '.join(weather_data.get('storm_details', []))}"
            if hail_data.get("has_hail_alert"):
                storm_info += f" HAIL ALERT: {hail_data.get('hail_size', 'confirmed')} size hail"
            trigger_outbound_call(
                lead_phone=phone, lead_name=name, lead_email=email,
                lead_context=f"Form submission. Service: {service}. Urgency: {urgency}. Location: {location}.{storm_info}",
            )
    except Exception as e:
        print(f"OUTBOUND CALL ERROR (non-blocking): {e}")

       

    # Auto-book inspection for qualified leads
    try:
        contractor = get_contractor_for_location(location)
        weather_context = ""
        if weather_data.get("has_storm"):
            weather_context = ", ".join(weather_data.get("storm_details", []))
        if hail_data.get("has_hail_alert"):
            weather_context += f" Hail: {hail_data.get('hail_size', 'confirmed')}"

        auto_book_if_qualified(
                lead_name=name, lead_phone=phone, lead_email=email,
                lead_location=location, lead_issue=issue,
                lead_temperature=lead_temperature,
                contractor_label=contractor["label"],
            )
    except Exception as e:
        print(f"AUTO-BOOK ERROR (non-blocking): {e}")

    try:
        send_smart_confirmation_sms(
            name=name, phone=phone, issue=issue,
            urgency=urgency, lead_temperature=lead_temperature,
        )
    except Exception as e:
        print(f"SMART SMS ERROR (non-blocking): {e}")

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

ADMIN_USERNAME = "kazfenadmin"
ADMIN_PASSWORD = "n53M+r9gA+B6Xrarun0p5w=="


@app.get("/admin-login", response_class=HTMLResponse)
def admin_login_page():
    return """
    <html>
    <head>
    <title>Admin Login</title>
    </head>
    <body style="font-family: sans-serif; background:#0b1020; color:white; display:flex; align-items:center; justify-content:center; height:100vh;">
        <form method="post" action="/admin-login" style="background:#111; padding:30px; border-radius:10px;">
            <h2>Admin Login</h2>
            <input name="username" placeholder="Username" style="display:block; margin-bottom:10px; padding:10px;" />
            <input name="password" type="password" placeholder="Password" style="display:block; margin-bottom:10px; padding:10px;" />
            <button type="submit" style="padding:10px;">Login</button>
        </form>
    </body>
    </html>
    """


@app.post("/admin-login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session["admin_logged_in"] = True
        return RedirectResponse(url="/leads", status_code=303)
    return RedirectResponse(url="/admin-login", status_code=303)


@app.get("/admin-logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin-login", status_code=303)

@app.get("/leads", response_class=HTMLResponse)
def view_leads(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin-login", status_code=303)
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

# ==============================================================================
# KAZFEN VOICE AI — 60-SECOND OUTBOUND LEAD QUALIFICATION
# ==============================================================================

VOICE_SYSTEM_PROMPT = """You are Kazfen's AI roofing assistant on a PHONE CALL. You have 60 seconds max.

RULES FOR PHONE CALLS:
- Keep every response under 3 sentences. People hate long phone AI.
- Sound warm, human, and fast. No filler words.
- Ask ONE question per turn. Never two.
- You are calling THEM — they already submitted interest. Acknowledge that.
- Do NOT ask for their name or phone — you already have it from the lead form.

YOUR GOAL (in order):
1. Confirm they need roofing help (1 turn)
2. Get the issue type: leak, storm damage, replacement, inspection (1 turn)
3. Get urgency: emergency or can wait? (1 turn)
4. Get property address or zip code (1 turn)
5. Ask about insurance: filed a claim or want help? (1 turn)
6. Confirm and close: "We'll have a roofing specialist reach out within the hour."

If they sound urgent (active leak, water damage), skip to essentials:
- Issue, address, and insurance. That's it. Move fast.

WHEN DONE qualifying, end your FINAL message with this exact block:

VOICE_LEAD_CAPTURED
Issue: <issue>
Urgency: <urgency>
Address: <address>
Insurance: <status>
Roof Type: <if mentioned>
Notes: <brief summary>

If the caller says they're not interested or asks to stop, say:
"No problem at all. If you ever need roofing help, we're here. Have a great day."
Then end with: VOICE_CALL_END
"""


def generate_voice_reply(call_sid: str, caller_speech: str) -> str:
    session = VOICE_SESSIONS.get(call_sid)
    if not session:
        return "Sorry, something went wrong. A specialist will call you back shortly."

    session["history"].append({"role": "user", "content": caller_speech})
    session["turn_count"] += 1

    messages = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}]

    lead_context = session.get("lead_context", "")
    if lead_context:
        messages.append({
            "role": "system",
            "content": f"Lead context from form submission: {lead_context}"
        })

    turns_left = session["max_turns"] - session["turn_count"]
    if turns_left <= 2:
        messages.append({
            "role": "system",
            "content": "You are running low on time. Wrap up qualification NOW. "
                       "Summarize what you have and close the call."
        })

    messages.extend(session["history"])

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=150,
            temperature=0.7,
        )
        ai_reply = response.choices[0].message.content or ""
    except Exception as e:
        print(f"VOICE AI ERROR: {e}")
        ai_reply = "Apologies, we're having a brief technical issue. A roofing specialist will call you back within the hour."

    session["history"].append({"role": "assistant", "content": ai_reply})
    return ai_reply


def process_voice_lead(call_sid: str, ai_reply: str):
    session = VOICE_SESSIONS.get(call_sid, {})
    lead_phone = session.get("lead_phone", "")
    lead_name = session.get("lead_name", "Unknown")
    lead_email = session.get("lead_email", "")

    issue = extract_field(ai_reply, "Issue") or "Phone inquiry"
    urgency = extract_field(ai_reply, "Urgency") or "Medium"
    address = extract_field(ai_reply, "Address") or ""
    insurance = extract_field(ai_reply, "Insurance") or "Not discussed"
    roof_type = extract_field(ai_reply, "Roof Type") or ""
    notes = extract_field(ai_reply, "Notes") or ""

    lead_score, lead_temperature = score_lead(
        issue=issue,
        urgency=urgency,
        insurance_status=insurance,
        inspection_timing="ASAP" if "urgent" in urgency.lower() else "This week",
        location=address,
        roof_type=roof_type,
    )

    contractor = get_contractor_for_location(address)

    save_lead(
        name=lead_name,
        phone=lead_phone,
        email=lead_email,
        location=address,
        roof_type=roof_type,
        issue=issue,
        urgency=urgency,
        insurance_status=insurance,
        inspection_timing="ASAP" if "urgent" in urgency.lower() else "This week",
        lead_score=lead_score,
        lead_temperature=lead_temperature,
        assigned_contractor=contractor["label"],
        assigned_email=contractor["email"],
        assigned_phone=contractor["phone"],
    )

    if lead_temperature in ["HOT", "WARM"]:
        send_sms_notification(
            contractor["phone"], contractor["label"],
            lead_name, lead_phone, lead_email, address,
            roof_type, issue, urgency, insurance,
            "ASAP", lead_score, lead_temperature,
        )

    print(f"VOICE LEAD SAVED: {lead_name} | {lead_phone} | Score: {lead_score} ({lead_temperature})")

    if call_sid in VOICE_SESSIONS:
        del VOICE_SESSIONS[call_sid]


def trigger_outbound_call(
    lead_phone: str,
    lead_name: str = "there",
    lead_email: str = "",
    lead_context: str = "",
):
    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")
        base_url = os.getenv("KAZFEN_BASE_URL", "https://kazfen.com")

        if not all([twilio_sid, twilio_auth, twilio_number, lead_phone]):
            print("OUTBOUND CALL SKIPPED: missing env vars or phone")
            return None

        twilio_client = Client(twilio_sid, twilio_auth)

        clean_phone = re.sub(r"[^\d+]", "", lead_phone)
        if not clean_phone.startswith("+"):
            if len(clean_phone) == 10:
                clean_phone = "+1" + clean_phone
            elif len(clean_phone) == 11 and clean_phone.startswith("1"):
                clean_phone = "+" + clean_phone

        call = twilio_client.calls.create(
            to=clean_phone,
            from_=twilio_number,
            url=f"{base_url}/twilio/voice/outbound?lead_name={requests.utils.quote(str(lead_name))}&lead_phone={clean_phone}&lead_email={requests.utils.quote(str(lead_email))}",
            method="POST",
            timeout=60,
            status_callback=f"{base_url}/twilio/voice/status",
            status_callback_method="POST",
        )

        VOICE_SESSIONS[call.sid] = {
            "call_sid": call.sid,
            "lead_phone": clean_phone,
            "lead_name": lead_name,
            "lead_email": lead_email,
            "lead_context": lead_context,
            "history": [],
            "started_at": time.time(),
            "turn_count": 0,
            "max_turns": 6,
            "qualified": False,
            "extracted_data": {},
        }

        print(f"OUTBOUND CALL INITIATED: {call.sid} -> {clean_phone}")
        return call.sid

    except Exception as e:
        print(f"OUTBOUND CALL ERROR: {e}")
        return None


# --- OUTBOUND CALL: First contact (Kazfen calls the lead) ---
@app.post("/twilio/voice/outbound")
async def twilio_voice_outbound(request: Request):
    params = request.query_params
    lead_name = params.get("lead_name", "there")
    lead_phone = params.get("lead_phone", "")
    lead_email = params.get("lead_email", "")

    form = await request.form()
    call_sid = str(form.get("CallSid", ""))

    if call_sid and call_sid not in VOICE_SESSIONS:
        VOICE_SESSIONS[call_sid] = {
            "call_sid": call_sid,
            "lead_phone": lead_phone,
            "lead_name": lead_name,
            "lead_email": lead_email,
            "lead_context": "",
            "history": [],
            "started_at": time.time(),
            "turn_count": 0,
            "max_turns": 6,
            "qualified": False,
            "extracted_data": {},
        }

    response = VoiceResponse()

    greeting = f"Hi {lead_name}, this is Kazfen's roofing assistant following up on your inquiry. I just have a couple quick questions to get you connected with the right specialist. What roofing issue are you dealing with?"

    if call_sid in VOICE_SESSIONS:
        VOICE_SESSIONS[call_sid]["history"].append({
            "role": "assistant", "content": greeting
        })

    gather = Gather(
        input="speech",
        action="/twilio/voice/conversation",
        method="POST",
        speech_timeout="auto",
        timeout=10,
    )
    gather.say(greeting, voice="Polly.Matthew")
    response.append(gather)

    response.say(
        "No worries — a roofing specialist will reach out to you shortly. Have a great day.",
        voice="Polly.Matthew"
    )

    return PlainTextResponse(str(response), media_type="application/xml")


# --- MULTI-TURN CONVERSATION LOOP ---
@app.post("/twilio/voice/conversation")
async def twilio_voice_conversation(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    speech_result = str(form.get("SpeechResult", "")).strip()

    response = VoiceResponse()

    if call_sid not in VOICE_SESSIONS:
        response.say(
            "Thanks for your time. A specialist will follow up with you shortly.",
            voice="Polly.Matthew"
        )
        return PlainTextResponse(str(response), media_type="application/xml")

    session = VOICE_SESSIONS[call_sid]

    elapsed = time.time() - session["started_at"]
    if elapsed > 65 or session["turn_count"] >= session["max_turns"]:
        ai_reply = "Thanks for all that info. We have everything we need. A roofing specialist will reach out within the hour. Have a great day."
        response.say(ai_reply, voice="Polly.Matthew")

        full_convo = " ".join([
            msg["content"] for msg in session["history"] if msg["role"] == "user"
        ])
        session["history"].append({"role": "user", "content": speech_result})
        process_voice_lead(call_sid, f"VOICE_LEAD_CAPTURED\nIssue: Phone inquiry\nUrgency: Medium\nAddress: Unknown\nInsurance: Not discussed\nNotes: {full_convo}")

        return PlainTextResponse(str(response), media_type="application/xml")

    ai_reply = generate_voice_reply(call_sid, speech_result or "No response")

    if "VOICE_LEAD_CAPTURED" in ai_reply:
        spoken_part = ai_reply.split("VOICE_LEAD_CAPTURED")[0].strip()
        if not spoken_part:
            spoken_part = "Perfect, we have everything we need. A roofing specialist will reach out within the hour. Thanks for your time."

        response.say(spoken_part, voice="Polly.Matthew")
        process_voice_lead(call_sid, ai_reply)

        return PlainTextResponse(str(response), media_type="application/xml")

    if "VOICE_CALL_END" in ai_reply:
        spoken_part = ai_reply.replace("VOICE_CALL_END", "").strip()
        response.say(spoken_part or "No problem. Have a great day.", voice="Polly.Matthew")

        if call_sid in VOICE_SESSIONS:
            del VOICE_SESSIONS[call_sid]

        return PlainTextResponse(str(response), media_type="application/xml")

    gather = Gather(
        input="speech",
        action="/twilio/voice/conversation",
        method="POST",
        speech_timeout="auto",
        timeout=8,
    )
    gather.say(ai_reply, voice="Polly.Matthew")
    response.append(gather)

    response.say(
        "I didn't catch that. No worries, a specialist will follow up with you shortly.",
        voice="Polly.Matthew"
    )

    return PlainTextResponse(str(response), media_type="application/xml")


# --- INBOUND CALL HANDLER (when someone calls YOUR Twilio number) ---
@app.post("/twilio/voice/inbound")
async def twilio_voice_inbound(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    caller = str(form.get("From", ""))

    VOICE_SESSIONS[call_sid] = {
        "call_sid": call_sid,
        "lead_phone": caller,
        "lead_name": "there",
        "lead_email": "",
        "lead_context": "Inbound call",
        "history": [],
        "started_at": time.time(),
        "turn_count": 0,
        "max_turns": 6,
        "qualified": False,
        "extracted_data": {},
    }

    response = VoiceResponse()

    greeting = "Thanks for calling Kazfen roofing. I'm an AI assistant here to get you connected with the right specialist fast. What roofing issue are you dealing with?"

    VOICE_SESSIONS[call_sid]["history"].append({
        "role": "assistant", "content": greeting
    })

    gather = Gather(
        input="speech",
        action="/twilio/voice/conversation",
        method="POST",
        speech_timeout="auto",
        timeout=10,
    )
    gather.say(greeting, voice="Polly.Matthew")
    response.append(gather)

    response.say("We didn't catch that. A specialist will follow up. Goodbye.", voice="Polly.Matthew")

    return PlainTextResponse(str(response), media_type="application/xml")


# --- CALL STATUS CALLBACK ---
@app.post("/twilio/voice/status")
async def twilio_voice_status(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    call_status = str(form.get("CallStatus", ""))

    print(f"CALL STATUS: {call_sid} -> {call_status}")

    if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
        if call_sid in VOICE_SESSIONS:
            session = VOICE_SESSIONS[call_sid]
            if not session.get("qualified") and session["history"]:
                full_convo = " ".join([
                    msg["content"] for msg in session["history"] if msg["role"] == "user"
                ])
                if full_convo.strip():
                    process_voice_lead(
                        call_sid,
                        f"VOICE_LEAD_CAPTURED\nIssue: Incomplete call ({call_status})\n"
                        f"Urgency: Medium\nAddress: Unknown\nInsurance: Not discussed\n"
                        f"Notes: Call ended with status '{call_status}'. Caller said: {full_convo}"
                    )
                else:
                    del VOICE_SESSIONS[call_sid]

    return JSONResponse({"status": "ok"})


# --- SMS HANDLER (single clean version) ---
@app.post("/twilio/sms")
async def twilio_sms(request: Request):
    form = await request.form()
    from_number = str(form.get("From", "")).strip()
    body = str(form.get("Body", "")).strip()

    # Handle opt-out
    if body.upper() in ["STOP", "UNSUBSCRIBE", "CANCEL", "QUIT"]:
        opt_out_phone(from_number)
        twiml = MessagingResponse()
        twiml.message("You've been unsubscribed from Kazfen messages. Reply START to re-subscribe.")
        return PlainTextResponse(str(twiml), media_type="application/xml")

    if from_number not in SMS_SESSIONS:
        SMS_SESSIONS[from_number] = []

    SMS_SESSIONS[from_number].append(body)

    ai_reply = generate_inbound_reply(body, channel="sms")

    SMS_SESSIONS[from_number].append(ai_reply)

    twiml = MessagingResponse()
    twiml.message(ai_reply)

    return PlainTextResponse(str(twiml), media_type="application/xml")


# --- MANUAL CALL TRIGGER (for dashboard + testing) ---
@app.post("/api/call-lead")
async def api_call_lead(request: Request):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()
    phone = data.get("phone", "")
    name = data.get("name", "there")
    email = data.get("email", "")
    context = data.get("context", "")

    if not phone:
        return JSONResponse({"error": "Phone number required"}, status_code=400)

    call_sid = trigger_outbound_call(
        lead_phone=phone,
        lead_name=name,
        lead_email=email,
        lead_context=context,
    )

    if call_sid:
        return {"status": "call_initiated", "call_sid": call_sid}
    else:
        return JSONResponse({"error": "Failed to initiate call"}, status_code=500)


# ==============================================================================
# KAZFEN UPGRADE #2: WEATHER/STORM API — TOMORROW.IO INTEGRATION
# ==============================================================================
#
# WHAT THIS DOES:
# - Checks real-time weather for every lead's location when they submit
# - If there's active storm/hail/high wind, auto-boosts lead score
# - Adds storm context to lead data (contractors LOVE knowing this)
# - New endpoint to check weather for any zip/city
# - Storm monitor endpoint for your target markets
#
# WHY TOMORROW.IO:
# - Free tier: 500 calls/day (plenty for launch)
# - Has severe weather, hail, and wind data
# - Simple REST API, no SDK needed
#
# SETUP:
# 1. Go to https://www.tomorrow.io/weather-api/
# 2. Sign up for free account
# 3. Copy your API key
# 4. Add to .env: TOMORROW_API_KEY=your_key_here
#
# NEW ENV VARIABLE:
#     TOMORROW_API_KEY=your_api_key_from_tomorrow_io
#
# PASTE THIS ENTIRE BLOCK INTO app.py:
# - Put it AFTER your Voice AI routes
# - BEFORE your Stripe checkout routes
# ==============================================================================


# --- STORM SEVERITY CONFIG ---
STORM_MARKETS = [
    {"city": "Kansas City", "state": "MO"},
    {"city": "Nashville", "state": "TN"},
    {"city": "Charlotte", "state": "NC"},
    {"city": "Indianapolis", "state": "IN"},
    {"city": "Grand Rapids", "state": "MI"},
]

WEATHER_CACHE = {}
WEATHER_CACHE_TTL = 1800  # 30 minutes — avoids burning API calls


def get_weather_for_location(location: str) -> dict:
    """
    Fetches real-time weather data for a location string (city, zip, address).
    Returns structured weather data with storm indicators.
    """
    import time as _time

    api_key = os.getenv("TOMORROW_API_KEY")
    if not api_key:
        print("WEATHER SKIPPED: missing TOMORROW_API_KEY")
        return {"error": "No API key", "has_storm": False}

    # Check cache
    cache_key = location.lower().strip()
    if cache_key in WEATHER_CACHE:
        cached = WEATHER_CACHE[cache_key]
        if _time.time() - cached["cached_at"] < WEATHER_CACHE_TTL:
            return cached["data"]

    try:
        url = "https://api.tomorrow.io/v4/weather/realtime"
        params = {
            "location": location,
            "apikey": api_key,
            "units": "imperial",
        }

        resp = requests.get(url, params=params, timeout=10)

        if resp.status_code != 200:
            print(f"WEATHER API ERROR: {resp.status_code} - {resp.text[:200]}")
            return {"error": f"API returned {resp.status_code}", "has_storm": False}

        data = resp.json()
        values = data.get("data", {}).get("values", {})

        # Extract key weather indicators
        wind_speed = values.get("windSpeed", 0)         # mph
        wind_gust = values.get("windGust", 0)            # mph
        precip_intensity = values.get("precipitationIntensity", 0)  # in/hr
        precip_type = values.get("precipitationType", 0)  # 0=none, 1=rain, 2=snow, 3=freezing rain, 4=ice/hail
        weather_code = values.get("weatherCode", 0)
        humidity = values.get("humidity", 0)
        temperature = values.get("temperature", 0)

        # Determine storm severity
        has_storm = False
        has_hail = False
        storm_severity = "none"
        storm_details = []

        # Hail detection
        if precip_type == 4:
            has_hail = True
            has_storm = True
            storm_severity = "severe"
            storm_details.append("Active hail")

        # High wind detection (50+ mph = severe, 30+ = moderate)
        if wind_gust >= 50 or wind_speed >= 40:
            has_storm = True
            storm_severity = "severe"
            storm_details.append(f"High winds: {wind_gust:.0f} mph gusts")
        elif wind_gust >= 30 or wind_speed >= 25:
            has_storm = True
            if storm_severity != "severe":
                storm_severity = "moderate"
            storm_details.append(f"Strong winds: {wind_gust:.0f} mph gusts")

        # Heavy rain detection
        if precip_intensity > 0.5:
            has_storm = True
            if storm_severity == "none":
                storm_severity = "moderate"
            storm_details.append(f"Heavy precipitation: {precip_intensity:.2f} in/hr")

        # Thunderstorm weather codes (Tomorrow.io codes 8xxx are thunderstorms)
        if weather_code >= 8000:
            has_storm = True
            if storm_severity != "severe":
                storm_severity = "moderate"
            storm_details.append("Thunderstorm activity")

        result = {
            "has_storm": has_storm,
            "has_hail": has_hail,
            "storm_severity": storm_severity,
            "storm_details": storm_details,
            "wind_speed": wind_speed,
            "wind_gust": wind_gust,
            "precip_intensity": precip_intensity,
            "precip_type": precip_type,
            "temperature": temperature,
            "humidity": humidity,
            "weather_code": weather_code,
            "location_queried": location,
        }

        # Cache it
        WEATHER_CACHE[cache_key] = {
            "data": result,
            "cached_at": _time.time(),
        }

        return result

    except Exception as e:
        print(f"WEATHER ERROR: {e}")
        return {"error": str(e), "has_storm": False}


def get_storm_score_boost(weather_data: dict) -> int:
    """
    Returns bonus points to add to lead score based on weather conditions.
    Storm = higher urgency = hotter lead.
    """
    if not weather_data or not weather_data.get("has_storm"):
        return 0

    severity = weather_data.get("storm_severity", "none")

    if severity == "severe":
        return 4  # Major boost — this lead is probably desperate
    elif severity == "moderate":
        return 2  # Moderate boost — likely has damage
    return 0


def get_storm_context_for_ai(weather_data: dict) -> str:
    """
    Returns a string to inject into AI prompts so the chatbot/voice AI
    knows about active weather in the lead's area.
    """
    if not weather_data or not weather_data.get("has_storm"):
        return ""

    details = ", ".join(weather_data.get("storm_details", []))
    severity = weather_data.get("storm_severity", "unknown")

    return (
        f"ACTIVE WEATHER ALERT for this lead's area: {details}. "
        f"Severity: {severity}. This lead may have fresh storm damage. "
        f"Prioritize urgency, mention that you're aware of recent weather in their area, "
        f"and fast-track them to inspection booking."
    )


# --- API ENDPOINT: Check weather for any location ---
@app.get("/api/weather")
async def api_weather(location: str):
    """
    Check weather for any location.
    GET /api/weather?location=Kansas City, MO
    GET /api/weather?location=66101
    """
    if not location:
        return JSONResponse({"error": "Location required"}, status_code=400)

    weather = get_weather_for_location(location)
    return weather


# --- API ENDPOINT: Storm monitor for all target markets ---
@app.get("/api/storm-monitor")
async def api_storm_monitor(request: Request):
    """
    Checks weather across all your target markets.
    Returns which cities have active storms — gold for outreach timing.
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = []
    for market in STORM_MARKETS:
        location = f"{market['city']}, {market['state']}"
        weather = get_weather_for_location(location)
        results.append({
            "city": market["city"],
            "state": market["state"],
            "has_storm": weather.get("has_storm", False),
            "storm_severity": weather.get("storm_severity", "none"),
            "storm_details": weather.get("storm_details", []),
            "wind_gust": weather.get("wind_gust", 0),
            "temperature": weather.get("temperature", 0),
        })

    # Sort: storms first, then by severity
    severity_order = {"severe": 0, "moderate": 1, "none": 2}
    results.sort(key=lambda x: severity_order.get(x["storm_severity"], 3))

    active_storms = [r for r in results if r["has_storm"]]

    return {
        "markets_checked": len(results),
        "active_storms": len(active_storms),
        "results": results,
    }

# ==============================================================================
# KAZFEN UPGRADE #3: HAIL DATA API — NWS ALERTS INTEGRATION
# ==============================================================================
#
# WHAT THIS DOES:
# - Checks NWS (National Weather Service) for active hail/severe weather alerts
# - Returns hail size, alert severity, and affected areas
# - Auto-boosts lead score when hail is detected in their area
# - Stacks ON TOP of Upgrade #2 (Tomorrow.io) for double weather intelligence
# - New endpoint to check hail alerts for any location
# - Hail monitor for all your target markets
#
# WHY NWS:
# - 100% FREE — no API key, no signup, no limits
# - Official US government severe weather data
# - Includes hail size in warnings (penny, quarter, golf ball, etc.)
# - Real-time alerts with affected zones
# - HailTrace/CoreLogic require enterprise contracts ($$$)
#
# SETUP:
# - No API key needed. No signup needed. Just paste and go.
#
# PASTE LOCATION:
# - Right AFTER your Upgrade #2 weather routes (/api/storm-monitor)
# - BEFORE your Stripe checkout routes
# ==============================================================================


import json

# --- NWS HAIL ALERT SEVERITY MAPPING ---
HAIL_SIZE_SCORES = {
    "quarter": 3,      # 1" hail — roof damage likely
    "half dollar": 3,
    "ping pong": 4,    # 1.5" — definite roof damage
    "golf": 4,         # 1.75" — significant damage
    "tennis": 5,       # 2.5" — severe damage
    "baseball": 5,     # 2.75" — extreme damage
    "softball": 5,     # 4"+ — catastrophic
}

NWS_CACHE = {}
NWS_CACHE_TTL = 900  # 15 minutes


def geocode_location_to_coords(location: str) -> dict:
    """
    Converts a city/state or zip code to lat/lon using the US Census geocoder.
    Free, no API key needed.
    """
    try:
        # Try zip code first
        clean = location.strip()
        if clean.isdigit() and len(clean) == 5:
            url = f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address={clean}&benchmark=Public_AR_Current&format=json"
        else:
            url = f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address={clean}&benchmark=Public_AR_Current&format=json"

        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return {}

        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return {}

        coords = matches[0].get("coordinates", {})
        return {
            "lat": coords.get("y", 0),
            "lon": coords.get("x", 0),
        }
    except Exception as e:
        print(f"GEOCODE ERROR: {e}")
        return {}


def get_nws_alerts_for_location(location: str) -> dict:
    """
    Fetches active NWS alerts (hail, severe thunderstorm, tornado) for a location.
    Uses the free NWS API — no key needed.
    """
    import time as _time

    cache_key = f"nws_{location.lower().strip()}"
    if cache_key in NWS_CACHE:
        cached = NWS_CACHE[cache_key]
        if _time.time() - cached["cached_at"] < NWS_CACHE_TTL:
            return cached["data"]

    try:
        # Step 1: Geocode the location
        coords = geocode_location_to_coords(location)
        if not coords:
            # Fallback: try NWS point lookup directly for known cities
            return _get_nws_alerts_by_state(location)

        lat = coords["lat"]
        lon = coords["lon"]

        # Step 2: Get NWS grid point
        point_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "Kazfen Lead Platform (notifications@kazfen.com)"}

        point_resp = requests.get(point_url, headers=headers, timeout=8)
        if point_resp.status_code != 200:
            return {"has_hail_alert": False, "alerts": [], "error": "NWS point lookup failed"}

        point_data = point_resp.json()
        county_zone = point_data.get("properties", {}).get("county", "")
        forecast_zone = point_data.get("properties", {}).get("forecastZone", "")

        # Step 3: Get active alerts for this zone
        zone_id = ""
        if forecast_zone:
            zone_id = forecast_zone.split("/")[-1]
        elif county_zone:
            zone_id = county_zone.split("/")[-1]

        if not zone_id:
            return {"has_hail_alert": False, "alerts": [], "error": "No zone found"}

        alerts_url = f"https://api.weather.gov/alerts/active?zone={zone_id}"
        alerts_resp = requests.get(alerts_url, headers=headers, timeout=8)

        if alerts_resp.status_code != 200:
            return {"has_hail_alert": False, "alerts": [], "error": "Alert fetch failed"}

        alerts_data = alerts_resp.json()
        features = alerts_data.get("features", [])

        return _process_nws_alerts(features, location, cache_key)

    except Exception as e:
        print(f"NWS ALERT ERROR: {e}")
        return {"has_hail_alert": False, "alerts": [], "error": str(e)}


def _get_nws_alerts_by_state(location: str) -> dict:
    """
    Fallback: fetch alerts by state abbreviation if geocoding fails.
    """
    import time as _time

    # Map common cities to state codes
    state_map = {
        "kansas city": "MO", "nashville": "TN", "charlotte": "NC",
        "indianapolis": "IN", "grand rapids": "MI", "miami": "FL",
        "new york": "NY", "los angeles": "CA", "dallas": "TX",
        "houston": "TX", "denver": "CO", "atlanta": "GA",
        "chicago": "IL", "phoenix": "AZ", "san antonio": "TX",
        "oklahoma city": "OK", "tulsa": "OK", "memphis": "TN",
        "st louis": "MO", "minneapolis": "MN",
    }

    location_lower = location.lower().strip()
    state_code = None

    for city, state in state_map.items():
        if city in location_lower:
            state_code = state
            break

    # Check if location itself is a state code
    if not state_code and len(location_lower) == 2:
        state_code = location_lower.upper()

    if not state_code:
        return {"has_hail_alert": False, "alerts": [], "error": "Could not determine state"}

    try:
        headers = {"User-Agent": "Kazfen Lead Platform (notifications@kazfen.com)"}
        alerts_url = f"https://api.weather.gov/alerts/active?area={state_code}"
        alerts_resp = requests.get(alerts_url, headers=headers, timeout=8)

        if alerts_resp.status_code != 200:
            return {"has_hail_alert": False, "alerts": [], "error": "State alert fetch failed"}

        alerts_data = alerts_resp.json()
        features = alerts_data.get("features", [])

        cache_key = f"nws_{location_lower}"
        return _process_nws_alerts(features, location, cache_key)

    except Exception as e:
        print(f"NWS STATE ALERT ERROR: {e}")
        return {"has_hail_alert": False, "alerts": [], "error": str(e)}


def _process_nws_alerts(features: list, location: str, cache_key: str) -> dict:
    """
    Process raw NWS alert features into structured hail/storm data.
    """
    import time as _time

    has_hail_alert = False
    has_severe_storm = False
    has_tornado = False
    hail_size = ""
    hail_score_boost = 0
    relevant_alerts = []

    for feature in features:
        props = feature.get("properties", {})
        event = props.get("event", "").lower()
        headline = props.get("headline", "")
        description = props.get("description", "").lower()
        severity = props.get("severity", "")
        urgency_val = props.get("urgency", "")

        # Check for hail-specific alerts
        is_hail_related = False

        if "hail" in event or "hail" in description:
            is_hail_related = True
            has_hail_alert = True

        if "severe thunderstorm" in event:
            has_severe_storm = True
            # Severe thunderstorm warnings often contain hail info
            if "hail" in description:
                is_hail_related = True
                has_hail_alert = True

        if "tornado" in event:
            has_tornado = True

        # Extract hail size from description
        detected_size = ""
        for size_name in HAIL_SIZE_SCORES:
            if size_name in description:
                detected_size = size_name
                size_boost = HAIL_SIZE_SCORES[size_name]
                if size_boost > hail_score_boost:
                    hail_score_boost = size_boost
                    hail_size = size_name
                break

        if is_hail_related or has_severe_storm or has_tornado:
            relevant_alerts.append({
                "event": props.get("event", ""),
                "headline": headline,
                "severity": severity,
                "urgency": urgency_val,
                "hail_size": detected_size,
                "areas": props.get("areaDesc", ""),
            })

    result = {
        "has_hail_alert": has_hail_alert,
        "has_severe_storm": has_severe_storm,
        "has_tornado": has_tornado,
        "hail_size": hail_size,
        "hail_score_boost": hail_score_boost,
        "alert_count": len(relevant_alerts),
        "alerts": relevant_alerts[:5],  # Cap at 5 most relevant
        "location_queried": location,
    }

    # Cache
    NWS_CACHE[cache_key] = {
        "data": result,
        "cached_at": _time.time(),
    }

    return result


def get_hail_score_boost(hail_data: dict) -> int:
    """
    Returns bonus points from hail alerts.
    Stacks with storm_boost from Upgrade #2.
    """
    if not hail_data or not hail_data.get("has_hail_alert"):
        if hail_data and hail_data.get("has_tornado"):
            return 5  # Tornado = max urgency
        if hail_data and hail_data.get("has_severe_storm"):
            return 2  # Severe storm without confirmed hail
        return 0

    return hail_data.get("hail_score_boost", 0)


def get_hail_context_for_ai(hail_data: dict) -> str:
    """
    Returns context string for AI prompts about active hail alerts.
    """
    if not hail_data:
        return ""

    parts = []

    if hail_data.get("has_hail_alert"):
        size = hail_data.get("hail_size", "unknown size")
        parts.append(f"ACTIVE HAIL ALERT: {size} size hail reported in the area")

    if hail_data.get("has_tornado"):
        parts.append("TORNADO WARNING active in the area")

    if hail_data.get("has_severe_storm") and not hail_data.get("has_hail_alert"):
        parts.append("Severe thunderstorm warning active")

    if not parts:
        return ""

    alert_text = ". ".join(parts)
    return (
        f"{alert_text}. This lead very likely has fresh roof damage. "
        f"Treat as HIGH PRIORITY. Mention awareness of severe weather in their area. "
        f"Fast-track to immediate inspection booking."
    )


# --- API ENDPOINT: Check hail alerts for any location ---
@app.get("/api/hail-check")
async def api_hail_check(location: str):
    """
    Check active hail/severe weather alerts for any location.
    GET /api/hail-check?location=Kansas City, MO
    GET /api/hail-check?location=66101
    """
    if not location:
        return JSONResponse({"error": "Location required"}, status_code=400)

    hail_data = get_nws_alerts_for_location(location)
    return hail_data


# --- API ENDPOINT: Hail monitor for all target markets ---
@app.get("/api/hail-monitor")
async def api_hail_monitor(request: Request):
    """
    Checks hail/severe weather alerts across all target markets.
    Shows which cities have active hail — perfect for timing outreach.
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = []
    for market in STORM_MARKETS:
        location = f"{market['city']}, {market['state']}"
        hail_data = get_nws_alerts_for_location(location)
        results.append({
            "city": market["city"],
            "state": market["state"],
            "has_hail_alert": hail_data.get("has_hail_alert", False),
            "has_severe_storm": hail_data.get("has_severe_storm", False),
            "has_tornado": hail_data.get("has_tornado", False),
            "hail_size": hail_data.get("hail_size", ""),
            "hail_score_boost": hail_data.get("hail_score_boost", 0),
            "alert_count": hail_data.get("alert_count", 0),
        })

    # Sort: hail first, then severe storms, then tornado
    results.sort(key=lambda x: (
        not x["has_hail_alert"],
        not x["has_tornado"],
        not x["has_severe_storm"],
    ))

    active_hail = [r for r in results if r["has_hail_alert"]]
    active_severe = [r for r in results if r["has_severe_storm"] or r["has_tornado"]]

    return {
        "markets_checked": len(results),
        "active_hail_alerts": len(active_hail),
        "active_severe_weather": len(active_severe),
        "results": results,
    }


from fastapi.responses import RedirectResponse
import stripe
import os

# ==============================================================================
# KAZFEN UPGRADE #4: AI LEAD QUALIFICATION — CLAUDE API
# ==============================================================================
#
# WHAT THIS DOES:
# - Every lead gets analyzed by Claude for intelligent scoring
# - Returns: score (1-10), temperature, qualification tags, recommended action
# - Factors in weather, hail, urgency, location, insurance, roof type
# - Way smarter than keyword matching — understands context and nuance
# - Falls back to your existing scoring if API fails (zero downtime risk)
#
# SETUP:
# 1. Go to https://console.anthropic.com/
# 2. Create an API key
# 3. Add to .env: ANTHROPIC_API_KEY=your_key_here
# 4. Install: pip install anthropic
#
# NEW ENV VARIABLE:
#     ANTHROPIC_API_KEY=your_api_key_here
#
# NEW DEPENDENCY:
#     pip install anthropic
#     (also add 'anthropic' to requirements.txt)
#
# PASTE LOCATION:
# - Right AFTER your Upgrade #3 hail routes (/api/hail-monitor)
# - BEFORE your Stripe checkout routes
# ==============================================================================

import anthropic


def ai_qualify_lead(
    name: str,
    phone: str,
    email: str,
    location: str,
    roof_type: str,
    issue: str,
    urgency: str,
    insurance_status: str,
    inspection_timing: str,
    message: str = "",
    weather_data: dict = None,
    hail_data: dict = None,
) -> dict:
    """
    Uses Claude to intelligently qualify and score a lead.
    Returns structured scoring data.
    Falls back to basic scoring if API fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("AI QUALIFICATION SKIPPED: missing ANTHROPIC_API_KEY")
        return None

    # Build weather context
    weather_context = "No weather data available."
    if weather_data and weather_data.get("has_storm"):
        details = ", ".join(weather_data.get("storm_details", []))
        weather_context = f"ACTIVE STORM in lead's area: {details}. Severity: {weather_data.get('storm_severity', 'unknown')}."

    hail_context = "No hail alerts."
    if hail_data and hail_data.get("has_hail_alert"):
        hail_context = f"ACTIVE HAIL ALERT: {hail_data.get('hail_size', 'unknown')} size hail. {hail_data.get('alert_count', 0)} active alerts."
    elif hail_data and hail_data.get("has_severe_storm"):
        hail_context = "Severe thunderstorm warning active (no confirmed hail yet)."

    prompt = f"""You are a roofing lead qualification AI. Analyze this lead and return a JSON score.

LEAD DATA:
- Name: {name}
- Phone: {phone}
- Email: {email}
- Location: {location}
- Roof Type: {roof_type}
- Issue: {issue}
- Urgency: {urgency}
- Insurance Status: {insurance_status}
- Inspection Timing: {inspection_timing}
- Additional Message: {message}

WEATHER CONDITIONS:
{weather_context}

HAIL DATA:
{hail_context}

SCORING RULES:
- Score 1-10 where 10 = highest value lead
- Active leak + storm area + insurance = 9-10
- Storm damage + wants inspection soon = 7-8
- General repair + medium urgency = 5-6
- Just browsing, no urgency = 1-3
- Active hail in their area ALWAYS adds +2 to base score (capped at 10)
- Insurance involvement ALWAYS adds +1
- Commercial property ALWAYS adds +1

QUALIFICATION TAGS (assign all that apply):
- emergency, storm_damage, hail_damage, insurance_claim, full_replacement
- repair, inspection, commercial, residential, high_value, time_sensitive

RESPOND WITH ONLY THIS JSON, NO OTHER TEXT:
{{
    "score": <1-10>,
    "temperature": "<HOT/WARM/COLD>",
    "tags": ["tag1", "tag2"],
    "recommended_action": "<one sentence: what the sales team should do>",
    "reasoning": "<one sentence: why this score>"
}}"""

    try:
        claude_client = anthropic.Anthropic(api_key=api_key)

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        reply = response.content[0].text.strip()

        # Parse JSON from response
        # Handle potential markdown code blocks
        if reply.startswith("```"):
            reply = reply.split("```")[1]
            if reply.startswith("json"):
                reply = reply[4:]
            reply = reply.strip()

        result = json.loads(reply)

        # Validate required fields
        score = min(max(int(result.get("score", 5)), 1), 10)
        temperature = result.get("temperature", "WARM").upper()
        if temperature not in ["HOT", "WARM", "COLD"]:
            temperature = "WARM"

        return {
            "score": score,
            "temperature": temperature,
            "tags": result.get("tags", []),
            "recommended_action": result.get("recommended_action", "Follow up within 24 hours"),
            "reasoning": result.get("reasoning", "AI analysis complete"),
            "ai_qualified": True,
        }

    except json.JSONDecodeError as e:
        print(f"AI QUALIFICATION JSON ERROR: {e}")
        print(f"Raw response: {reply}")
        return None

    except Exception as e:
        print(f"AI QUALIFICATION ERROR: {e}")
        return None


# --- API ENDPOINT: Manually qualify/re-qualify a lead ---
@app.post("/api/qualify-lead")
async def api_qualify_lead(request: Request):
    """
    Manually trigger AI qualification for a lead.
    POST /api/qualify-lead
    Body: {"name": "...", "phone": "...", "location": "...", etc.}
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()

    # Get weather + hail for location
    location = data.get("location", "")
    weather_data = {}
    hail_data = {}
    if location:
        weather_data = get_weather_for_location(location)
        hail_data = get_nws_alerts_for_location(location)

    result = ai_qualify_lead(
        name=data.get("name", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        location=location,
        roof_type=data.get("roof_type", ""),
        issue=data.get("issue", ""),
        urgency=data.get("urgency", ""),
        insurance_status=data.get("insurance_status", ""),
        inspection_timing=data.get("inspection_timing", ""),
        message=data.get("message", ""),
        weather_data=weather_data,
        hail_data=hail_data,
    )

    if result:
        return result
    else:
        return JSONResponse({"error": "AI qualification failed"}, status_code=500)



# ==============================================================================
# KAZFEN UPGRADE #5: GOOGLE CALENDAR — AUTOMATIC INSPECTION BOOKING
# ==============================================================================
#
# WHAT THIS DOES:
# - Auto-books inspection slots for HOT leads
# - Checks contractor calendar for availability
# - Creates calendar events with full lead details
# - Sends confirmation SMS to the lead with booking time
# - API endpoint to check available slots
# - API endpoint to manually book a slot from dashboard
#
# SETUP:
# 1. Go to https://console.cloud.google.com/
# 2. Create a new project (or use existing)
# 3. Enable "Google Calendar API" (APIs & Services → Library → search "Calendar")
# 4. Create a Service Account (APIs & Services → Credentials → Create Credentials → Service Account)
# 5. Download the JSON key file
# 6. Rename it to google-service-account.json
# 7. Upload it to your project root (same folder as app.py)
# 8. Share your Google Calendar with the service account email
#    (it looks like: something@project-id.iam.gserviceaccount.com)
#    Give it "Make changes to events" permission
# 9. Update .env with your calendar ID
#
# ENV VARIABLES (update these — you already have placeholders):
#     GOOGLE_CALENDAR_ID=your-calendar-id@group.calendar.google.com
#     GOOGLE_SERVICE_ACCOUNT_FILE=google-service-account.json
#     BOOKING_TIMEZONE=America/New_York
#     BOOKING_SLOT_MINUTES=60
#
# NEW DEPENDENCIES:
#     pip install google-api-python-client google-auth pytz
#     (also add all three to requirements.txt)
#
# PASTE LOCATION:
# - Right AFTER your Upgrade #4 AI qualification routes (/api/qualify-lead)
# - BEFORE your Stripe checkout routes
# ==============================================================================

from datetime import datetime, timedelta
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    import pytz
    GOOGLE_CALENDAR_AVAILABLE = True
except ImportError:
    print("Google Calendar libraries not available - calendar features disabled")
    GOOGLE_CALENDAR_AVAILABLE = False

def get_calendar_service():
    """
    Creates and returns an authenticated Google Calendar API service.
    """
    credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "google-service-account.json")

    if not os.path.exists(credentials_file):
        print(f"CALENDAR ERROR: {credentials_file} not found")
        return None

    try:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=credentials)
        return service
    except Exception as e:
        print(f"CALENDAR AUTH ERROR: {e}")
        return None


def get_available_slots(
    date_str: str = None,
    days_ahead: int = 5,
    slot_minutes: int = None,
) -> list:
    """
    Returns available booking slots for the next N days.
    Checks the contractor's calendar and finds open windows.
    """
    service = get_calendar_service()
    if not service:
        return []

    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    timezone_str = os.getenv("BOOKING_TIMEZONE", "America/New_York")
    slot_minutes = slot_minutes or int(os.getenv("BOOKING_SLOT_MINUTES", "60"))

    if not calendar_id:
        print("CALENDAR ERROR: missing GOOGLE_CALENDAR_ID")
        return []

    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)

    if date_str:
        try:
            start_date = tz.localize(datetime.strptime(date_str, "%Y-%m-%d"))
        except ValueError:
            start_date = now + timedelta(days=1)
    else:
        start_date = now + timedelta(days=1)

    end_date = start_date + timedelta(days=days_ahead)

    business_start_hour = 8
    business_end_hour = 17

    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_date.isoformat(),
            timeMax=end_date.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        existing_events = events_result.get("items", [])

        busy_times = []
        for event in existing_events:
            start = event.get("start", {})
            end = event.get("end", {})
            start_time = start.get("dateTime")
            end_time = end.get("dateTime")

            if start_time and end_time:
                busy_start = datetime.fromisoformat(start_time)
                busy_end = datetime.fromisoformat(end_time)
                busy_times.append((busy_start, busy_end))

        available_slots = []
        current_date = start_date

        while current_date < end_date:
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            slot_time = current_date.replace(
                hour=business_start_hour, minute=0, second=0, microsecond=0
            )
            day_end = current_date.replace(
                hour=business_end_hour, minute=0, second=0, microsecond=0
            )

            while slot_time + timedelta(minutes=slot_minutes) <= day_end:
                slot_end = slot_time + timedelta(minutes=slot_minutes)

                is_available = True
                for busy_start, busy_end in busy_times:
                    if slot_time < busy_end and slot_end > busy_start:
                        is_available = False
                        break

                if slot_time > now and is_available:
                    available_slots.append({
                        "start": slot_time.isoformat(),
                        "end": slot_end.isoformat(),
                        "date": slot_time.strftime("%A, %B %d"),
                        "time": slot_time.strftime("%I:%M %p"),
                        "display": f"{slot_time.strftime('%A, %B %d at %I:%M %p')} ({timezone_str})",
                    })

                slot_time += timedelta(minutes=slot_minutes)

            current_date += timedelta(days=1)

        return available_slots

    except Exception as e:
        print(f"CALENDAR SLOTS ERROR: {e}")
        return []


def book_inspection(
    lead_name: str,
    lead_phone: str,
    lead_email: str,
    lead_location: str,
    lead_issue: str,
    lead_temperature: str,
    slot_start: str,
    slot_end: str = None,
    contractor_label: str = "Roofing Specialist",
) -> dict:
    """
    Books an inspection on the contractor's Google Calendar.
    """
    service = get_calendar_service()
    if not service:
        return {"success": False, "error": "Calendar service unavailable"}

    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    timezone_str = os.getenv("BOOKING_TIMEZONE", "America/New_York")
    slot_minutes = int(os.getenv("BOOKING_SLOT_MINUTES", "60"))

    if not calendar_id:
        return {"success": False, "error": "No calendar ID configured"}

    try:
        start_dt = datetime.fromisoformat(slot_start)

        if slot_end:
            end_dt = datetime.fromisoformat(slot_end)
        else:
            end_dt = start_dt + timedelta(minutes=slot_minutes)

        event = {
            "summary": f"Roof Inspection — {lead_name} [{lead_temperature}]",
            "location": lead_location,
            "description": (
                f"KAZFEN AUTO-BOOKED INSPECTION\n\n"
                f"Lead: {lead_name}\n"
                f"Phone: {lead_phone}\n"
                f"Email: {lead_email}\n"
                f"Location: {lead_location}\n"
                f"Issue: {lead_issue}\n"
                f"Temperature: {lead_temperature}\n"
                f"Assigned: {contractor_label}\n\n"
                f"— Auto-booked by Kazfen AI"
            ),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone_str,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone_str,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15},
                ],
            },
        }

        if lead_email and "@" in lead_email:
            event["attendees"] = [{"email": lead_email}]

        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates="all",
        ).execute()

        booking_time = start_dt.strftime("%A, %B %d at %I:%M %p")

        return {
            "success": True,
            "event_id": created_event.get("id"),
            "event_link": created_event.get("htmlLink"),
            "booking_time": booking_time,
            "timezone": timezone_str,
        }

    except Exception as e:
        print(f"BOOKING ERROR: {e}")
        return {"success": False, "error": str(e)}


def auto_book_if_qualified(
    lead_name: str,
    lead_phone: str,
    lead_email: str,
    lead_location: str,
    lead_issue: str,
    lead_temperature: str,
    contractor_label: str = "Roofing Specialist",
) -> dict:
    """
    Automatically books the next available slot for HOT leads.
    Sends confirmation SMS to the lead.
    """
    if not GOOGLE_CALENDAR_AVAILABLE:
        print("AUTO-BOOK SKIPPED: Google Calendar not available")
        return None
    if lead_temperature not in ["HOT"]:
        return {"success": False, "reason": "Auto-booking only for HOT leads"}

    slots = get_available_slots(days_ahead=3)
    if not slots:
        print("AUTO-BOOK: No available slots found")
        return {"success": False, "reason": "No available slots"}

    next_slot = slots[0]
    result = book_inspection(
        lead_name=lead_name,
        lead_phone=lead_phone,
        lead_email=lead_email,
        lead_location=lead_location,
        lead_issue=lead_issue,
        lead_temperature=lead_temperature,
        slot_start=next_slot["start"],
        slot_end=next_slot["end"],
        contractor_label=contractor_label,
    )

    if result.get("success") and lead_phone:
        try:
            twilio_sid = os.getenv("TWILIO_SID")
            twilio_auth = os.getenv("TWILIO_AUTH")
            twilio_number = os.getenv("TWILIO_NUMBER")

            if twilio_sid and twilio_auth and twilio_number:
                twilio_client = Client(twilio_sid, twilio_auth)

                clean_phone = re.sub(r"[^\d+]", "", lead_phone)
                if not clean_phone.startswith("+"):
                    if len(clean_phone) == 10:
                        clean_phone = "+1" + clean_phone
                    elif len(clean_phone) == 11 and clean_phone.startswith("1"):
                        clean_phone = "+" + clean_phone

                sms_body = (
                    f"Hi {lead_name}, your roof inspection has been booked for "
                    f"{result['booking_time']} ({result['timezone']}). "
                    f"A roofing specialist will be at {lead_location}. "
                    f"Reply to this message if you need to reschedule. — Kazfen"
                )

                twilio_client.messages.create(
                    body=sms_body,
                    from_=twilio_number,
                    to=clean_phone,
                )
                print(f"BOOKING SMS SENT to {clean_phone}")

        except Exception as e:
            print(f"BOOKING SMS ERROR: {e}")

    return result


# --- API ENDPOINT: Get available slots ---
@app.get("/api/calendar/slots")
async def api_calendar_slots(days_ahead: int = 5):
    """
    GET /api/calendar/slots?days_ahead=5
    """
    slots = get_available_slots(days_ahead=days_ahead)
    return {
        "available_slots": len(slots),
        "slots": slots[:20],
    }


# --- API ENDPOINT: Book an inspection manually ---
@app.post("/api/calendar/book")
async def api_calendar_book(request: Request):
    """
    POST /api/calendar/book
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()

    result = book_inspection(
        lead_name=data.get("name", "Lead"),
        lead_phone=data.get("phone", ""),
        lead_email=data.get("email", ""),
        lead_location=data.get("location", ""),
        lead_issue=data.get("issue", "Inspection"),
        lead_temperature=data.get("temperature", "WARM"),
        slot_start=data.get("slot_start", ""),
        contractor_label=data.get("contractor", "Roofing Specialist"),
    )

    return result


# --- API ENDPOINT: Auto-book next available for a lead ---
@app.post("/api/calendar/auto-book")
async def api_calendar_auto_book(request: Request):
    """
    POST /api/calendar/auto-book
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()

    result = auto_book_hot_lead(
        lead_name=data.get("name", "Lead"),
        lead_phone=data.get("phone", ""),
        lead_email=data.get("email", ""),
        lead_location=data.get("location", ""),
        lead_issue=data.get("issue", "Inspection"),
        lead_temperature=data.get("temperature", "HOT"),
        contractor_label=data.get("contractor", "Roofing Specialist"),
    )

    return result


# ==============================================================================
# KAZFEN UPGRADE #6: TWILIO SMS UPGRADES
# ==============================================================================
#
# WHAT THIS DOES:
# - Smarter customer confirmation SMS with lead-specific details
# - Auto follow-up SMS for leads that haven't responded (24hr, 48hr)
# - Two-way SMS: leads can reply and get AI responses
# - Booking confirmation via SMS when inspection is auto-booked
# - Storm alert SMS to leads in affected areas
# - Opt-out handling (STOP keyword)
#
# NO NEW ENV VARIABLES NEEDED — uses your existing Twilio setup
#
# PASTE LOCATION:
# - Right AFTER your Upgrade #5 Google Calendar routes
# - BEFORE your Stripe checkout routes
# ==============================================================================


# --- SMS OPT-OUT TRACKING ---
SMS_OPT_OUTS = set()  # In production, store this in a database


def is_opted_out(phone: str) -> bool:
    """Check if a phone number has opted out of SMS."""
    clean = re.sub(r"[^\d+]", "", phone)
    return clean in SMS_OPT_OUTS


def opt_out_phone(phone: str):
    """Add phone to opt-out list."""
    clean = re.sub(r"[^\d+]", "", phone)
    SMS_OPT_OUTS.add(clean)
    print(f"SMS OPT-OUT: {clean}")


def send_smart_confirmation_sms(
    name: str,
    phone: str,
    issue: str,
    urgency: str,
    lead_temperature: str,
    booking_info: dict = None,
):
    """
    Sends a smarter confirmation SMS based on lead context.
    Different messages for HOT vs WARM vs COLD leads.
    Includes booking info if auto-booked.
    """
    if is_opted_out(phone):
        print(f"SMS SKIPPED (opted out): {phone}")
        return

    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")

        if not all([twilio_sid, twilio_auth, twilio_number, phone]):
            print("SMART SMS SKIPPED: missing env vars")
            return

        twilio_client = Client(twilio_sid, twilio_auth)

        # Build contextual message
        if booking_info:
            sms_body = (
                f"Hi {name}, thanks for reaching out to Kazfen! "
                f"Your roof inspection is confirmed for "
                f"{booking_info['date']} at {booking_info['time']}. "
                f"A specialist will arrive at your property. "
                f"Reply to this number with any questions. — Kazfen"
            )
        elif lead_temperature == "HOT":
            sms_body = (
                f"Hi {name}, we received your roofing request and it's been "
                f"marked as high priority. A roofing specialist will call you "
                f"within the next 30 minutes. If urgent, reply here. — Kazfen"
            )
        elif lead_temperature == "WARM":
            sms_body = (
                f"Hi {name}, thanks for contacting Kazfen about your roof. "
                f"A specialist will follow up within a few hours. "
                f"Reply here if you have any questions. — Kazfen"
            )
        else:
            sms_body = (
                f"Hi {name}, thanks for your interest in Kazfen roofing services. "
                f"We'll be in touch soon. Reply here anytime. — Kazfen"
            )

        message = twilio_client.messages.create(
            body=sms_body,
            from_=twilio_number,
            to=phone,
        )
        print(f"SMART CONFIRMATION SMS sent: {message.sid}")

    except Exception as e:
        print(f"SMART SMS ERROR: {e}")


def send_storm_alert_sms(
    phone: str,
    name: str,
    location: str,
    storm_details: str,
):
    """
    Sends storm alert SMS to leads in affected areas.
    Great for re-engaging cold leads after a storm hits their area.
    """
    if is_opted_out(phone):
        return

    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")

        if not all([twilio_sid, twilio_auth, twilio_number, phone]):
            return

        twilio_client = Client(twilio_sid, twilio_auth)

        sms_body = (
            f"Hi {name}, severe weather ({storm_details}) has been reported "
            f"near {location}. If your roof sustained any damage, reply YES "
            f"for a free inspection. — Kazfen Roofing"
        )

        message = twilio_client.messages.create(
            body=sms_body,
            from_=twilio_number,
            to=phone,
        )
        print(f"STORM ALERT SMS sent: {message.sid}")

    except Exception as e:
        print(f"STORM ALERT SMS ERROR: {e}")


def send_follow_up_sms(
    phone: str,
    name: str,
    follow_up_type: str = "24hr",
):
    """
    Sends follow-up SMS to leads that haven't responded.
    """
    if is_opted_out(phone):
        return

    try:
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_auth = os.getenv("TWILIO_AUTH")
        twilio_number = os.getenv("TWILIO_NUMBER")

        if not all([twilio_sid, twilio_auth, twilio_number, phone]):
            return

        twilio_client = Client(twilio_sid, twilio_auth)

        if follow_up_type == "24hr":
            sms_body = (
                f"Hi {name}, just following up on your roofing inquiry. "
                f"Do you still need help? Reply YES to schedule a free inspection. — Kazfen"
            )
        elif follow_up_type == "48hr":
            sms_body = (
                f"Hi {name}, we want to make sure your roof is taken care of. "
                f"Reply YES and we'll get a specialist out to you this week. — Kazfen"
            )
        else:
            sms_body = (
                f"Hi {name}, last check-in from Kazfen. If you still need roofing help, "
                f"reply anytime. We're here when you're ready. — Kazfen"
            )

        message = twilio_client.messages.create(
            body=sms_body,
            from_=twilio_number,
            to=phone,
        )
        print(f"FOLLOW-UP SMS ({follow_up_type}) sent: {message.sid}")

    except Exception as e:
        print(f"FOLLOW-UP SMS ERROR: {e}")


# --- API ENDPOINT: Send storm alerts to leads in a specific area ---
@app.post("/api/send-storm-alerts")
async def api_send_storm_alerts(request: Request):
    """
    Send storm alert SMS to all leads in a specific location.
    POST /api/send-storm-alerts
    Body: {"location": "Kansas City, MO"}
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()
    target_location = data.get("location", "").lower()

    if not target_location:
        return JSONResponse({"error": "Location required"}, status_code=400)

    # Check if there's actually a storm
    weather = get_weather_for_location(target_location)
    hail = get_nws_alerts_for_location(target_location)

    storm_details = ""
    if weather.get("has_storm"):
        storm_details = ", ".join(weather.get("storm_details", []))
    if hail.get("has_hail_alert"):
        storm_details += f", hail ({hail.get('hail_size', 'confirmed')})"

    if not storm_details:
        return {"message": "No active storms in this area", "alerts_sent": 0}

    # Read all leads and find matches
    leads = read_all_leads()
    alerts_sent = 0

    for lead in leads:
        lead_location = (lead.get("location", "") or "").lower()
        if target_location in lead_location or lead_location in target_location:
            phone = lead.get("phone", "")
            name = lead.get("name", "")
            if phone and name:
                send_storm_alert_sms(
                    phone=phone,
                    name=name,
                    location=lead.get("location", target_location),
                    storm_details=storm_details,
                )
                alerts_sent += 1

    return {
        "message": f"Storm alerts sent to {alerts_sent} leads in {target_location}",
        "alerts_sent": alerts_sent,
        "storm_details": storm_details,
    }


# --- API ENDPOINT: Send follow-up to a specific lead ---
@app.post("/api/send-followup")
async def api_send_followup(request: Request):
    """
    Send follow-up SMS to a specific lead.
    POST /api/send-followup
    Body: {"phone": "+1...", "name": "John", "type": "24hr"}
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()
    phone = data.get("phone", "")
    name = data.get("name", "there")
    follow_up_type = data.get("type", "24hr")

    if not phone:
        return JSONResponse({"error": "Phone required"}, status_code=400)

    send_follow_up_sms(phone=phone, name=name, follow_up_type=follow_up_type)

    return {"message": f"Follow-up ({follow_up_type}) sent to {phone}"}


# --- API ENDPOINT: Bulk follow-up to all leads with a specific status ---
@app.post("/api/bulk-followup")
async def api_bulk_followup(request: Request):
    """
    Send follow-up SMS to all leads with a given status.
    POST /api/bulk-followup
    Body: {"status": "New", "type": "24hr"}
    Admin-only.
    """
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json()
    target_status = data.get("status", "New")
    follow_up_type = data.get("type", "24hr")

    leads = read_all_leads()
    sent_count = 0

    for lead in leads:
        if lead.get("status", "New") == target_status:
            phone = lead.get("phone", "")
            name = lead.get("name", "there")
            if phone:
                send_follow_up_sms(
                    phone=phone,
                    name=name,
                    follow_up_type=follow_up_type,
                )
                sent_count += 1

    return {
        "message": f"Sent {follow_up_type} follow-up to {sent_count} leads with status '{target_status}'",
        "sent_count": sent_count,
    }


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

    # Checkout completed (new customer signup)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")

        print("New customer email:", customer_email)
        print("Customer ID:", customer_id)
        print("Subscription ID:", subscription_id)

    # Recurring subscription payment successful
    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")
        subscription_id = invoice.get("subscription")

        print("Invoice paid")
        print("Customer:", customer_id)
        print("Subscription:", subscription_id)

    # Payment failed
    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")

        print("Payment failed for customer:", customer_id)

    # Subscription cancelled
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        print("Subscription cancelled:", subscription.get("id"))

    return {"status": "success"}

app.mount("/", StaticFiles(directory="static", html=True), name="static")


