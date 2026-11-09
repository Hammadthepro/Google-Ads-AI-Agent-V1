"""Fictional, clearly SIMULATED fixture data for websites, Google Business Profiles and PageSpeed.

All businesses, people, reviews and domains (*.example) are invented for testing.
"""
from __future__ import annotations

import hashlib


def _schema(name, phone, city, region, btype="LocalBusiness"):
    return {"@context": "https://schema.org", "@type": btype, "name": name, "telephone": phone,
            "address": {"@type": "PostalAddress", "addressLocality": city, "addressRegion": region}}


def _site(name, phone, city, region, services: dict[str, str], *, gtag=True, gtm=True, aw=True, btype="LocalBusiness",
          extra_pages=None, missing=(), status=None, thin=(), claims_text="Licensed and insured.", form=True):
    """services: {service name: path}. `missing` services are linked but have no page (404)."""
    scripts = []
    if gtm:
        scripts.append("<script>(function(w,d,s,l,i){w[l]=w[l]||[];})(window,document,'script','dataLayer','GTM-SIM1234');</script>")
    if gtag:
        ids = ["G-SIM9F2K1"] + (["AW-1234567890"] if aw else [])
        scripts.append(f'<script async src="https://www.googletagmanager.com/gtag/js?id={ids[0]}"></script>')
        scripts.append("<script>" + "".join(f"gtag('config','{i}');" for i in ids) + "</script>")
    menu = [("Home", "/")] + [(s, p) for s, p in services.items()] + [("Contact", "/contact")]
    pages = {
        "/": {"title": f"{name} | {city}, {region}", "meta": f"{name} serves {city} with {', '.join(list(services)[:3])}. Call {phone}.",
              "h1": f"{name} — trusted in {city}", "h2": list(services), "h3": ["Why choose us", "Service areas"],
              "text": f"{name} provides {', '.join(services)} across {city} and nearby areas. {claims_text} "
                      f"Call {phone} or request a quote online. Serving {city}, {region} and surrounding suburbs.",
              "phones": [phone], "form": form, "schema": _schema(name, phone, city, region, btype)},
        "/contact": {"title": f"Contact {name}", "meta": f"Contact {name} in {city}.", "h1": "Request a quote",
                     "h2": ["Call us", "Send a message"], "h3": [], "text": f"Call {phone}. We respond within one business day.",
                     "phones": [phone], "form": form, "schema": None},
        "/thank-you": {"title": "Thank you", "meta": "", "h1": "Thanks — we'll be in touch", "h2": [], "h3": [],
                       "text": "Your request was received.", "phones": [phone], "form": False, "schema": None},
    }
    for svc, path in services.items():
        if svc in missing:
            continue
        depth = "short" if svc in thin else "long"
        body = (f"{svc} in {city}. " + (f"Our {svc.lower()} specialists handle every job from inspection to completion. "
                                         f"Upfront pricing, fast scheduling and a clear explanation of options. {claims_text} " * (1 if depth == "short" else 4)))
        pages[path] = {"title": f"{svc} in {city} | {name}", "meta": f"{svc} by {name}. Call {phone}.",
                       "h1": f"{svc} in {city}", "h2": [f"What our {svc.lower()} includes", "Pricing", "FAQ"],
                       "h3": ["How soon can you come out?", "Do you offer warranties?"], "text": body,
                       "phones": [phone], "form": form, "schema": None}
    for path, page in (extra_pages or {}).items():
        pages[path] = page
    return {"name": name, "scripts": scripts, "menu": menu, "pages": pages, "status": status or {},
            "robots": "User-agent: *\nDisallow: /admin/\nSitemap: {base}/sitemap.xml\n"}


SITES: dict[str, dict] = {
    "www.northsideplumbing.example": _site(
        "Northside Plumbing", "(555) 201-4400", "Columbus", "OH",
        {"Drain Cleaning": "/drain-cleaning", "Water Heater Repair": "/water-heater-repair",
         "Emergency Plumbing": "/emergency-plumbing", "Leak Detection": "/leak-detection",
         "Sewer Line Repair": "/sewer-line-repair"},
        missing=("Leak Detection",), btype="Plumber",
        claims_text="Licensed and insured. Same-day service. Family owned since 2004."),
    "www.northsidehvac.example": _site(
        "Northside Heating & Air", "(555) 201-4410", "Columbus", "OH",
        {"AC Repair": "/ac-repair", "Furnace Repair": "/furnace-repair", "HVAC Maintenance": "/hvac-maintenance"},
        thin=("HVAC Maintenance",), btype="HVACBusiness", claims_text="Licensed and insured technicians."),
    "www.brightsmiledental.example": _site(
        "Brightsmile Dental", "(555) 340-7720", "Phoenix", "AZ",
        {"Dental Implants": "/dental-implants", "Teeth Whitening": "/teeth-whitening",
         "Emergency Dentist": "/emergency-dentist", "Invisalign": "/invisalign"},
        aw=False, gtm=False, thin=("Invisalign",), btype="Dentist",
        claims_text="Board-certified dentists. Most insurance accepted."),
    "www.summitinjurylaw.example": _site(
        "Summit Injury Law", "(555) 610-3300", "Denver", "CO",
        {"Car Accident Lawyer": "/car-accident-lawyer", "Personal Injury Lawyer": "/personal-injury",
         "Truck Accident Lawyer": "/truck-accident-lawyer", "Wrongful Death Lawyer": "/wrongful-death"},
        status={"/personal-injury": 503}, btype="LegalService",
        claims_text="No fee unless we win. Free consultation."),
}

COMPETITOR_SITES = {
    "www.rapidrooter.example": ("Rapid Rooter Columbus", "(555) 201-9001", "Columbus", "OH",
                                {"Drain Cleaning": "/drains", "Emergency Plumbing": "/24-7-plumber", "Sewer Line Repair": "/sewer",
                                 "Hydro Jetting": "/hydro-jetting"}, "24/7 emergency service. $49 drain special."),
    "www.bluelineplumbing.example": ("BlueLine Plumbing Co", "(555) 201-9002", "Columbus", "OH",
                                     {"Water Heater Repair": "/water-heaters", "Tankless Water Heaters": "/tankless",
                                      "Drain Cleaning": "/drain-cleaning"}, "Upfront pricing. 1-year labor warranty."),
    "www.buckeyedrain.example": ("Buckeye Drain Pros", "(555) 201-9003", "Columbus", "OH",
                                 {"Drain Cleaning": "/drain-cleaning", "Camera Inspection": "/camera-inspection"},
                                 "Locally owned. Senior discounts."),
    "www.coolaircolumbus.example": ("CoolAir Columbus", "(555) 201-9101", "Columbus", "OH",
                                    {"AC Repair": "/ac-repair", "AC Installation": "/ac-install", "Heat Pumps": "/heat-pumps"},
                                    "Same-day AC repair. Financing available."),
    "www.comfortfirst.example": ("Comfort First Heating", "(555) 201-9102", "Columbus", "OH",
                                 {"Furnace Repair": "/furnace", "HVAC Maintenance": "/maintenance-plans"},
                                 "Maintenance plans from $15/month."),
    "www.arcticprohvac.example": ("ArcticPro HVAC", "(555) 201-9103", "Columbus", "OH",
                                  {"AC Repair": "/ac", "Furnace Repair": "/heating"}, "Licensed, NATE-certified technicians."),
    "www.desertsmile.example": ("Desert Smile Dental", "(555) 340-9201", "Phoenix", "AZ",
                                {"Dental Implants": "/implants", "Emergency Dentist": "/emergency", "Veneers": "/veneers"},
                                "Free implant consultation. Same-day crowns."),
    "www.camelbackfamily.example": ("Camelback Family Dentistry", "(555) 340-9202", "Phoenix", "AZ",
                                    {"Teeth Whitening": "/whitening", "Invisalign": "/invisalign", "Pediatric Dentistry": "/kids"},
                                    "In-network with most PPO plans."),
    "www.sonorandental.example": ("Sonoran Dental Studio", "(555) 340-9203", "Phoenix", "AZ",
                                  {"Dental Implants": "/dental-implants", "Invisalign": "/clear-aligners"},
                                  "Sedation dentistry available."),
    "www.frontrangeinjury.example": ("Front Range Injury Attorneys", "(555) 610-9301", "Denver", "CO",
                                     {"Car Accident Lawyer": "/car-accidents", "Motorcycle Accident Lawyer": "/motorcycle",
                                      "Personal Injury Lawyer": "/personal-injury"}, "Over $100M recovered. No fee unless we win."),
    "www.milehighaccident.example": ("Mile High Accident Law", "(555) 610-9302", "Denver", "CO",
                                     {"Truck Accident Lawyer": "/trucks", "Car Accident Lawyer": "/cars"}, "Free case review 24/7."),
    "www.peaklegalgroup.example": ("Peak Legal Group", "(555) 610-9303", "Denver", "CO",
                                   {"Personal Injury Lawyer": "/injury", "Wrongful Death Lawyer": "/wrongful-death"},
                                   "Former insurance defense attorneys."),
}
for host, (n, ph, city, reg, svcs, claims) in COMPETITOR_SITES.items():
    SITES[host] = _site(n, ph, city, reg, svcs, claims_text=claims)


def _reviews(name: str, good: bool) -> list[dict]:
    if good:
        texts = ["Showed up on time and fixed it fast. Fair price.", "Friendly team, explained everything clearly.",
                 "Would hire again — great communication."]
        ratings = [5, 5, 4]
    else:
        texts = ["Hard to get someone on the phone.", "Work was fine but scheduling took a week.",
                 "Good result, front desk could be friendlier."]
        ratings = [3, 4, 3]
    return [{"author": f"Simulated reviewer {i + 1}", "rating": r, "text": t, "relative_time": f"{i + 1} weeks ago",
             "simulated": True} for i, (r, t) in enumerate(zip(ratings, texts))]


def _place(name, host, phone, address, category, rating, count, photos, good=True, hours=None):
    return {"place_id": "sim_" + hashlib.md5(name.encode()).hexdigest()[:12], "name": name,
            "website": f"https://{host}/", "phone": phone, "address": address, "primary_category": category,
            "rating": rating, "user_rating_count": count, "photo_count": photos,
            "opening_hours": hours or {"weekday_text": ["Mon-Fri 7:00-19:00", "Sat 8:00-16:00", "Sun closed"]},
            "business_status": "OPERATIONAL", "reviews": _reviews(name, good), "simulated": True}


PLACES: list[dict] = [
    _place("Northside Plumbing", "www.northsideplumbing.example", "(555) 201-4400", "1200 Example Ave, Columbus, OH", "Plumber", 4.7, 168, 42),
    _place("Northside Heating & Air", "www.northsidehvac.example", "(555) 201-4410", "1200 Example Ave, Columbus, OH", "HVAC contractor", 4.4, 37, 9, good=False),
    _place("Brightsmile Dental", "www.brightsmiledental.example", "(555) 340-7720", "88 Sample Rd, Phoenix, AZ", "Dentist", 4.1, 12, 4, good=False),
    _place("Summit Injury Law", "www.summitinjurylaw.example", "(555) 610-3300", "500 Fictional St, Denver, CO", "Personal injury attorney", 4.9, 214, 18),
    _place("Rapid Rooter Columbus", "www.rapidrooter.example", "(555) 201-9001", "Columbus, OH", "Plumber", 4.6, 412, 120),
    _place("BlueLine Plumbing Co", "www.bluelineplumbing.example", "(555) 201-9002", "Columbus, OH", "Plumber", 4.8, 233, 61),
    _place("Buckeye Drain Pros", "www.buckeyedrain.example", "(555) 201-9003", "Columbus, OH", "Plumber", 4.5, 89, 22),
    _place("CoolAir Columbus", "www.coolaircolumbus.example", "(555) 201-9101", "Columbus, OH", "HVAC contractor", 4.8, 305, 77),
    _place("Comfort First Heating", "www.comfortfirst.example", "(555) 201-9102", "Columbus, OH", "HVAC contractor", 4.6, 198, 40),
    _place("ArcticPro HVAC", "www.arcticprohvac.example", "(555) 201-9103", "Columbus, OH", "HVAC contractor", 4.7, 151, 35),
    _place("Desert Smile Dental", "www.desertsmile.example", "(555) 340-9201", "Phoenix, AZ", "Dentist", 4.8, 540, 96),
    _place("Camelback Family Dentistry", "www.camelbackfamily.example", "(555) 340-9202", "Phoenix, AZ", "Dentist", 4.7, 288, 54),
    _place("Sonoran Dental Studio", "www.sonorandental.example", "(555) 340-9203", "Phoenix, AZ", "Dentist", 4.9, 201, 63),
    _place("Front Range Injury Attorneys", "www.frontrangeinjury.example", "(555) 610-9301", "Denver, CO", "Personal injury attorney", 4.8, 390, 25),
    _place("Mile High Accident Law", "www.milehighaccident.example", "(555) 610-9302", "Denver, CO", "Personal injury attorney", 4.6, 120, 14),
    _place("Peak Legal Group", "www.peaklegalgroup.example", "(555) 610-9303", "Denver, CO", "Personal injury attorney", 4.7, 96, 11),
]

PAGESPEED: dict[str, dict] = {
    "www.northsideplumbing.example": {"mobile": 71, "desktop": 92},
    "www.northsidehvac.example": {"mobile": 58, "desktop": 86},
    "www.brightsmiledental.example": {"mobile": 34, "desktop": 69},
    "www.summitinjurylaw.example": {"mobile": 63, "desktop": 90},
}
