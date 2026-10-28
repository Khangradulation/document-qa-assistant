"""Generates data/greenfield_hydroponics_guide.pdf.

The content is ORIGINAL, fictional text written for this project (no third-party
copyright). Run with:  .venv/bin/python eval/make_sample_pdf.py
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PAGES = {
    "Greenfield Hydroponics Starter Guide - 1. Introduction": [
        "This guide is written for beginners and covers three systems: Deep Water",
        "Culture (DWC), Nutrient Film Technique (NFT) and Ebb and Flow.",
        "We recommend starting with DWC when growing lettuce.",
        "Lettuce is usually ready to harvest 28 to 35 days after transplanting.",
        "Safety: wear gloves when handling pH Down, which contains phosphoric acid.",
        "Always add acid to water slowly, never water to acid.",
    ],
    "2. Water and Nutrients": [
        "Keep the nutrient solution at a pH between 5.8 and 6.2.",
        "For lettuce the target EC is 1.2 to 1.8 mS/cm.",
        "Replace the whole reservoir solution every 14 days.",
        "Water temperature should stay between 18 and 22 degrees Celsius.",
        "Above 24 degrees Celsius there is a high risk of root rot caused by Pythium.",
        "Run the air stone 24 hours a day to keep dissolved oxygen high.",
    ],
    "3. Lighting": [
        "Lettuce needs 14 to 16 hours of light per day.",
        "Tomatoes need 16 to 18 hours of light per day.",
        "Seedlings should receive 18 hours of light during their first week.",
        "Place full-spectrum LED lights 30 cm above the plant canopy.",
        "Aim for a light intensity of 200 to 250 micromoles per square metre per second.",
    ],
    "4. Troubleshooting and Maintenance": [
        "Yellow lower leaves usually indicate a nitrogen deficiency.",
        "Tip burn is caused by EC that is too high or by poor airflow.",
        "Slimy brown roots mean root rot: lower the temperature and add",
        "3 percent hydrogen peroxide at 3 ml per litre.",
        "Algae grows when light reaches the reservoir, so cover it.",
        "Control aphids by spraying neem oil once a week.",
        "Maintenance schedule: check pH daily and check EC every 2 days.",
    ],
}


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "data" / "greenfield_hydroponics_guide.pdf"
    out.parent.mkdir(exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=A4, invariant=True)
    for title, lines in PAGES.items():
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, 780, title)
        c.setFont("Helvetica", 11)
        y = 750
        for line in lines:
            c.drawString(50, y, line)
            y -= 18
        c.showPage()
    c.save()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
