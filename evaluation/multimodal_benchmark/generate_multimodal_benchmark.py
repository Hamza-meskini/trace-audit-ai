"""Generate the TraceAudit multimodal diagnostic benchmark.

The corpus is deliberately synthetic so it can be shared with hosted models.
Its labels are authored before generation and are not embedded in prompts.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "documents"
ASSETS = ROOT / "assets"
PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#1F6FB2")
TEAL = colors.HexColor("#0F8B8D")
INK = colors.HexColor("#1E293B")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#CBD5E1")
PAPER = colors.HexColor("#F8FAFC")
AMBER = colors.HexColor("#D97706")
RED = colors.HexColor("#B42318")
GREEN = colors.HexColor("#087A55")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _line(draw: ImageDraw.ImageDraw, points, fill, width=3):
    draw.line(points, fill=fill, width=width, joint="curve")


def build_visual_assets() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    # System architecture diagram: deterministic relationships, no compliance claim.
    image = Image.new("RGB", (1400, 720), "white")
    d = ImageDraw.Draw(image)
    title = _font(40, True)
    body = _font(27, True)
    small = _font(22)
    d.text((55, 36), "Aquila HV safety and monitoring architecture", fill="#16324F", font=title)
    boxes = {
        "Battery modules": (70, 170, 360, 320),
        "BMS controller": (555, 170, 845, 320),
        "Main contactors": (1040, 170, 1330, 320),
        "Isolation monitor": (300, 455, 590, 605),
        "Vehicle CAN": (810, 455, 1100, 605),
    }
    fills = ["#E8F1FA", "#DDF4F3", "#FFF3DD", "#F0EAFE", "#E8F1FA"]
    for (label, box), fill in zip(boxes.items(), fills):
        d.rounded_rectangle(box, radius=22, fill=fill, outline="#1F6FB2", width=4)
        bbox = d.textbbox((0, 0), label, font=body)
        d.text(((box[0]+box[2]-bbox[2])/2, box[1]+45), label, fill="#16324F", font=body)
    arrows = [((360,245),(555,245)), ((845,245),(1040,245)), ((445,455),(650,320)), ((955,455),(750,320))]
    for start, end in arrows:
        _line(d, [start, end], "#0F8B8D", 7)
        angle = math.atan2(end[1]-start[1], end[0]-start[0])
        for delta in (2.55, -2.55):
            p = (end[0] + 24*math.cos(angle+delta), end[1] + 24*math.sin(angle+delta))
            _line(d, [end, p], "#0F8B8D", 7)
    d.text((70, 650), "Figure 1. Logical context only; acceptance is defined by the numbered requirements.", fill="#64748B", font=small)
    image.save(ASSETS / "system_architecture.png", quality=95)

    # Crash event timing plot.
    image = Image.new("RGB", (1400, 780), "white")
    d = ImageDraw.Draw(image)
    d.text((60, 35), "Crash input, contactor state and DC-bus decay", fill="#16324F", font=title)
    left, top, right, bottom = 120, 130, 1320, 650
    _line(d, [(left,top),(left,bottom),(right,bottom)], "#334155", 4)
    for ms in range(0, 6001, 1000):
        x = left + (right-left)*ms/6000
        _line(d, [(x,bottom),(x,bottom+12)], "#334155", 3)
        d.text((x-18,bottom+20), str(ms), fill="#475569", font=small)
    d.text((575, 720), "Time after crash signal (ms)", fill="#334155", font=small)
    # Voltage trace decays from 812 V to 48 V at 4.2 s.
    pts=[]
    for ms in range(0,6001,100):
        volts = 812 if ms < 14 else 812*math.exp(-(ms-14)/1480)
        y = bottom - (bottom-top-40)*min(volts,850)/850
        x = left + (right-left)*ms/6000
        pts.append((x,y))
    _line(d, pts, "#1F6FB2", 6)
    x14 = left+(right-left)*14/6000
    _line(d, [(x14,top),(x14,bottom)], "#B42318", 3)
    d.text((x14+12, top+20), "Contactors open: 14.6 ms", fill="#B42318", font=small)
    x42 = left+(right-left)*4200/6000
    y48 = bottom-(bottom-top-40)*48/850
    d.ellipse((x42-9,y48-9,x42+9,y48+9), fill="#087A55")
    d.text((x42-180,y48-55), "48 V at 4.20 s", fill="#087A55", font=small)
    d.text((130, 96), "DC bus voltage (V)", fill="#1F6FB2", font=small)
    image.save(ASSETS / "crash_timing_plot.png", quality=95)

    # Thermal trace intentionally omits a recovery cycle.
    image = Image.new("RGB", (1400, 780), "white")
    d = ImageDraw.Draw(image)
    d.text((60, 35), "Thermal protection characterization - heating phase", fill="#16324F", font=title)
    left, top, right, bottom = 120, 130, 1320, 650
    _line(d, [(left,top),(left,bottom),(right,bottom)], "#334155", 4)
    for minute in range(0, 13, 2):
        x=left+(right-left)*minute/12
        _line(d,[(x,bottom),(x,bottom+12)],"#334155",3)
        d.text((x-8,bottom+18),str(minute),fill="#475569",font=small)
    pts=[]
    for minute in [i/10 for i in range(121)]:
        temp=25+5.2*minute
        x=left+(right-left)*minute/12
        y=bottom-(bottom-top-40)*(temp-20)/80
        pts.append((x,y))
    _line(d,pts,"#D97706",6)
    shut_min=(87-25)/5.2
    x=left+(right-left)*shut_min/12
    y=bottom-(bottom-top-40)*(87-20)/80
    d.ellipse((x-10,y-10,x+10,y+10),fill="#B42318")
    d.text((x-305,y-55),"Shutdown asserted at 87 C",fill="#B42318",font=small)
    d.text((535,720),"Elapsed heating time (min)",fill="#334155",font=small)
    d.text((120,92),"Temperature (deg C)",fill="#D97706",font=small)
    d.text((850,105),"No cool-down/recovery phase recorded",fill="#64748B",font=small)
    image.save(ASSETS / "thermal_shutdown_plot.png", quality=95)

    # CAN heartbeat latency plot.
    image = Image.new("RGB", (1400, 780), "white")
    d = ImageDraw.Draw(image)
    d.text((60, 35), "CAN heartbeat interval over 30-minute endurance run", fill="#16324F", font=title)
    left, top, right, bottom = 120, 130, 1320, 650
    _line(d,[(left,top),(left,bottom),(right,bottom)],"#334155",4)
    limit_y=bottom-(bottom-top-40)*(100-80)/25
    _line(d,[(left,limit_y),(right,limit_y)],"#B42318",4)
    d.text((1040,limit_y-38),"100 ms requirement",fill="#B42318",font=small)
    pts=[]
    for minute in [i/10 for i in range(301)]:
        interval=92.5+1.9*math.sin(minute*1.7)+0.7*math.sin(minute*5.2)
        x=left+(right-left)*minute/30
        y=bottom-(bottom-top-40)*(interval-80)/25
        pts.append((x,y))
    _line(d,pts,"#0F8B8D",5)
    d.text((510,720),"Elapsed endurance time (min)",fill="#334155",font=small)
    d.text((120,92),"Heartbeat interval (ms)",fill="#0F8B8D",font=small)
    image.save(ASSETS / "can_heartbeat_plot.png", quality=95)

    # Connector cross-section for the destructive inspection note.
    image = Image.new("RGB", (1400, 720), "white")
    d = ImageDraw.Draw(image)
    d.text((60, 35), "J17 connector destructive inspection cross-section", fill="#16324F", font=title)
    d.rounded_rectangle((160,170,1240,560),radius=45,fill="#E2E8F0",outline="#334155",width=5)
    d.rounded_rectangle((300,245,1080,485),radius=30,fill="#F97316",outline="#7C2D12",width=6)
    d.rectangle((520,280,880,450),fill="#111827",outline="#020617",width=4)
    d.ellipse((875,355,940,420),fill="#38BDF8",outline="#0369A1",width=3)
    d.text((950,365),"0.6 mL moisture",fill="#0369A1",font=small)
    d.text((170,595),"Figure 2. Blue marker indicates collected liquid behind the rear boot after opening.",fill="#64748B",font=small)
    image.save(ASSETS / "connector_ingress_diagram.png", quality=95)


BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.2, leading=12, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=7.7, leading=9.5, textColor=MUTED)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.8, leading=9.2, textColor=INK)
CELL_HEAD = ParagraphStyle("cellhead", fontName="Helvetica-Bold", fontSize=7.8, leading=9.2, textColor=colors.white)


def p(text: str, style=BODY) -> Paragraph:
    return Paragraph(text, style)


def new_page(c: canvas.Canvas, title: str, doc_id: str, page: int, total: int, section: str) -> None:
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H-25*mm, PAGE_W, 25*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(18*mm, PAGE_H-14*mm, title)
    c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W-18*mm, PAGE_H-14*mm, doc_id)
    tab_font_size = 7.2
    tab_width = min(max(pdfmetrics.stringWidth(section.upper(), "Helvetica-Bold", tab_font_size) + 8*mm, 55*mm), 100*mm)
    c.setFillColor(BLUE)
    c.rect(18*mm, PAGE_H-34*mm, tab_width, 6*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", tab_font_size)
    c.drawString(21*mm, PAGE_H-32*mm, section.upper())
    c.setStrokeColor(LINE)
    c.line(18*mm, 16*mm, PAGE_W-18*mm, 16*mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(18*mm, 10.5*mm, "Synthetic engineering benchmark - no real product or certification claim")
    c.drawRightString(PAGE_W-18*mm, 10.5*mm, f"Page {page} of {total}")


def heading(c: canvas.Canvas, text: str, y: float, size: int = 16) -> float:
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", size)
    c.drawString(18*mm, y, text)
    return y-8*mm


def paragraph(c: canvas.Canvas, text: str, y: float, width: float = 174*mm, style=BODY) -> float:
    para = p(text, style)
    _, h = para.wrap(width, 100*mm)
    para.drawOn(c, 18*mm, y-h)
    return y-h-4*mm


def note(c: canvas.Canvas, label: str, text: str, y: float, tone: str = "blue") -> float:
    palette = {"blue": (colors.HexColor("#E8F1FA"), BLUE), "amber": (colors.HexColor("#FFF3DD"), AMBER), "red": (colors.HexColor("#FDECEC"), RED), "green": (colors.HexColor("#E7F6F0"), GREEN)}
    fill, accent = palette[tone]
    para = p(f"<b>{label}</b> {text}")
    _, h = para.wrap(164*mm, 100*mm)
    box_h = h+8*mm
    c.setFillColor(fill); c.roundRect(18*mm, y-box_h, 174*mm, box_h, 3*mm, fill=1, stroke=0)
    c.setFillColor(accent); c.rect(18*mm, y-box_h, 3*mm, box_h, fill=1, stroke=0)
    para.drawOn(c, 24*mm, y-h-4*mm)
    return y-box_h-5*mm


def draw_table(c: canvas.Canvas, caption: str, rows: list[list[str]], widths: list[float], y: float, repeat=False) -> float:
    c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 9)
    c.drawString(18*mm, y, caption)
    y -= 4*mm
    data = [[p(str(cell), CELL_HEAD) for cell in rows[0]]]
    data += [[p(str(cell), CELL) for cell in row] for row in rows[1:]]
    table = Table(data, colWidths=widths, repeatRows=1 if repeat else 0, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.45, LINE), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F3F7FA")]),
    ]))
    _, h = table.wrap(sum(widths), 200*mm)
    table.drawOn(c, 18*mm, y-h)
    return y-h-6*mm


def req_card(c: canvas.Canvas, req_id: str, title: str, text: str, conditions: list[str], y: float) -> float:
    c.setFillColor(colors.white); c.setStrokeColor(LINE)
    cond_html = "<br/>".join(f"<b>{item.split(':',1)[0]}:</b>{item.split(':',1)[1]}" for item in conditions)
    para = p(f"<font color='#1F6FB2'><b>{req_id} - {title}</b></font><br/>{text}<br/><br/>{cond_html}")
    _, h = para.wrap(164*mm, 120*mm)
    box_h = h+10*mm
    c.roundRect(18*mm, y-box_h, 174*mm, box_h, 3*mm, fill=1, stroke=1)
    para.drawOn(c, 23*mm, y-h-5*mm)
    return y-box_h-6*mm


def figure(c: canvas.Canvas, path: Path, caption_text: str, y: float, width: float = 174*mm) -> float:
    with Image.open(path) as im:
        aspect = im.height/im.width
    height = width*aspect
    c.drawImage(str(path), 18*mm, y-height, width=width, height=height, preserveAspectRatio=True, mask="auto")
    y -= height+2*mm
    cap = p(caption_text, SMALL); _, h = cap.wrap(width, 30*mm); cap.drawOn(c, 18*mm, y-h)
    return y-h-5*mm


def make_requirements_pdf() -> None:
    path = DOCS / "01_Aquila_BMS_System_Requirements.pdf"
    c = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    total=7; title="Aquila BMS System Requirements"
    new_page(c,title,"AQL-SRS-042 Rev C",1,total,"Document control")
    y=PAGE_H-48*mm; y=heading(c,"High-voltage safety and controls",y,22)
    y=paragraph(c,"Product-level verification specification for the synthetic Aquila 800 V battery system. Numbered clauses are mandatory unless an explicit logic gate states otherwise.",y)
    y=draw_table(c,"Table 1. Document control",[["Field","Value"],["Document type","Controlled system specification"],["Authority","Aquila Systems Engineering"],["Revision / effective date","Rev C / 2026-06-15"],["Verification evidence","Approved test reports and signed inspection records"]],[48*mm,126*mm],y)
    figure(c,ASSETS/"system_architecture.png","Figure 1. System architecture context. The diagram is informative; requirements remain normative.",y,160*mm)
    c.showPage()
    pages=[
        (2,"Electrical operating envelope",[("MM-REQ-001","Pack voltage monitoring","The BMS shall continuously monitor pack voltage throughout 400 V to 800 V DC.",["C1: lower bound coverage shall be at or below 400 V","C2: upper bound coverage shall be at or above 800 V"]),("MM-REQ-002","Cell measurement accuracy","Cell-voltage measurement error shall not exceed +/-5 mV from -20 deg C through +60 deg C.",["C1: absolute error shall be <= 5 mV","C2: the verified temperature range shall include -20 deg C","C3: the verified temperature range shall include +60 deg C"]) ]),
        (3,"Crash electrical safety",[("MM-REQ-003","Crash disconnect response","On a validated crash input, both main contactors shall open within 20 ms and the DC bus shall fall below 60 V within 5.0 s.",["C1: contactor opening latency <= 20 ms","C2: DC bus voltage < 60 V by 5.0 s"]),("MM-REQ-004","Post-impact isolation","At 60 s after impact, measured isolation resistance divided by the measured DC-bus voltage shall be at least 500 ohm/V.",["C1: measurement taken at 60 s after impact","C2: calculated isolation ratio >= 500 ohm/V"]) ]),
        (4,"Fluid integrity and thermal protection",[("MM-REQ-005","Coolant circuit integrity","The coolant circuit shall hold 2.0 bar for 30 minutes with no visible leakage.",["C1: pressure >= 2.0 bar for >= 30 min","C2: no visible coolant leakage"]),("MM-REQ-007","Thermal shutdown and recovery","The BMS shall command shutdown at or above 85 deg C and shall permit recovery only after temperature is below 70 deg C for 60 s.",["C1: shutdown asserted at temperature >= 85 deg C","C2: recovery requires temperature < 70 deg C continuously for 60 s"]) ]),
        (5,"Visual marking and communications",[("MM-REQ-006","Service-disconnect hazard marking","Every service-disconnect cover shall carry a readable yellow triangular warning label with a black lightning symbol before the cover can be accessed.",["C1: marking design is yellow triangle with black lightning symbol","C2: the marking is present and readable on every service-disconnect cover"]),("MM-REQ-008","CAN heartbeat endurance","The BMS CAN heartbeat interval shall not exceed 100 ms during a continuous 30-minute run, with zero lost heartbeat messages.",["C1: maximum heartbeat interval <= 100 ms","C2: zero lost messages during >= 30 min"]) ]),
        (6,"Ingress protection and metrology",[("MM-REQ-009","Connector J17 ingress protection","Connector J17 shall remain IP67 after 1 m water immersion for 30 minutes, with no moisture inside the rear boot.",["C1: immersion depth >= 1 m for >= 30 min","C2: no moisture inside rear boot"]),("MM-REQ-010","Instrument traceability","Every measuring instrument used for acceptance data shall be identified by serial number and have calibration valid within 12 months on the test date.",["C1: each acceptance measurement identifies an instrument serial","C2: each identified instrument has calibration age <= 12 months"]) ]),
        (7,"Conditional and alternative safety logic",[("MM-REQ-011","Drive-away inhibition","If the charge connector is engaged, commanded propulsion torque shall remain zero and vehicle movement shall not exceed 150 mm.",["C1: charge-engaged propulsion torque = 0 Nm","C2: charge-engaged vehicle movement <= 150 mm"]),("MM-REQ-012","Alternative post-impact electrical protection","After frontal impact, electrical protection is acceptable when either the isolation ratio is at least 500 ohm/V or an isolation fault causes the main contactors to open within 100 ms.",["C1: post-impact isolation ratio >= 500 ohm/V","C2: isolation-fault contactor opening latency <= 100 ms"]) ])
    ]
    for page,section,cards in pages:
        new_page(c,title,"AQL-SRS-042 Rev C",page,total,section); y=PAGE_H-47*mm
        y=heading(c,section,y)
        for rid,rt,txt,conds in cards: y=req_card(c,rid,rt,txt,conds,y)
        if page==7:
            note(c,"Logic rule.","MM-REQ-011 is IF_THEN. MM-REQ-012 is ANY_OF: proving either alternative is sufficient, but an untested alternative must still remain visible in the audit trace.",y,"blue")
        c.showPage()
    c.save()


def make_test_report_pdf() -> None:
    path=DOCS/"02_Aquila_Design_Validation_Test_Report.pdf"; c=canvas.Canvas(str(path),pagesize=A4,pageCompression=1)
    total=8; title="Aquila Design Validation Test Report"
    new_page(c,title,"AQL-DVTR-118 Rev A",1,total,"Approval and scope"); y=PAGE_H-48*mm
    y=heading(c,"Design validation record",y,22)
    y=paragraph(c,"Executed laboratory results for prototype pack AQL-P3-017. This signed report is primary test evidence. Inspection after disassembly is recorded separately in AQL-QIR-221.",y)
    y=draw_table(c,"Table 1. Approval",[["Role","Name / status"],["Test engineer","M. Chen - signed 2026-07-19"],["Independent reviewer","L. Ortega - signed 2026-07-20"],["Test article","AQL-P3-017"],["Laboratory","North Cell DV Laboratory"]],[52*mm,122*mm],y)
    note(c,"Scope boundary.","A PASS in this report applies only to the recorded test article and method. Later destructive-inspection observations remain authoritative for conditions they directly inspect.",y,"amber")
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",2,total,"TR-EL-01 voltage and accuracy"); y=PAGE_H-47*mm; y=heading(c,"TR-EL-01 - Electrical measurement characterization",y)
    y=draw_table(c,"Table 2. Pack monitoring sweep",[["Set point","Displayed voltage","Coverage result","Instrument"],["380 V","380.3 V","Below 400 V lower bound","DMM-22"],["400 V","400.2 V","Required lower bound","DMM-22"],["800 V","799.8 V","Required upper bound","DMM-22"],["820 V","819.6 V","Above 800 V upper bound","DMM-22"]],[34*mm,40*mm,65*mm,35*mm],y)
    y=draw_table(c,"Table 3. Cell-voltage error by chamber point",[["Temperature","Worst absolute error","Acceptance","Coverage note"],["+25 deg C","4.2 mV","<= 5 mV","Tested"],["+60 deg C","4.8 mV","<= 5 mV","Tested upper endpoint"],["-20 deg C","Not measured","No result","Cold chamber unavailable"]],[36*mm,45*mm,38*mm,55*mm],y)
    note(c,"Result.","Pack monitoring covers 380-820 V. Cell accuracy is demonstrated at +25 and +60 deg C; the required -20 deg C endpoint was not tested.",y,"blue")
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",3,total,"TR-SF-02 crash response"); y=PAGE_H-47*mm; y=heading(c,"TR-SF-02 - Frontal-impact electrical response",y)
    y=draw_table(c,"Table 4. Event timing and discharge",[["Event","Measured value","Requirement","Result"],["Crash input asserted","0.0 ms","Reference","Recorded"],["Both main contactors open","14.6 ms","<= 20 ms","PASS"],["DC bus at 4.20 s","48 V","< 60 V by 5.0 s","PASS"]],[56*mm,38*mm,48*mm,32*mm],y)
    figure(c,ASSETS/"crash_timing_plot.png","Figure 2. Recorded DC-bus trace; contactors opened at 14.6 ms and bus voltage reached 48 V at 4.20 s.",y,160*mm)
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",4,total,"TR-ISO-03 measurement record"); y=PAGE_H-47*mm; y=heading(c,"TR-ISO-03 - Post-impact isolation measurement",y)
    y=paragraph(c,"The following values were recorded exactly 60 seconds after the frontal-impact pulse. Calculation and acceptance are continued on page 5.",y)
    y=draw_table(c,"Table 5. Isolation measurement operands",[["Quantity","Symbol","Recorded value","Instrument"],["Elapsed time after impact","t","60.0 s","DAQ-14"],["DC-bus voltage","Vbus","402 V","DMM-22"],["Isolation resistance","Riso","1.26 Mohm","HI-07"]],[55*mm,30*mm,48*mm,41*mm],y)
    note(c,"Cross-page record.","Do not decide the ohm-per-volt criterion from this page alone. The controlled formula, converted units and computed ratio appear on page 5.",y,"amber")
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",5,total,"TR-ISO-03 calculation and alternative path"); y=PAGE_H-47*mm; y=heading(c,"TR-ISO-03 - Calculation and logic-path result",y)
    y=note(c,"Controlled calculation.","Riso/Vbus = 1,260,000 ohm / 402 V = 3,134 ohm/V. Acceptance threshold: >= 500 ohm/V. Result: PASS.",y,"green")
    y=draw_table(c,"Table 6. MM-REQ-012 alternative paths",[["Alternative path","Evidence","Status"],["A - post-impact isolation ratio >= 500 ohm/V","3,134 ohm/V from pages 4-5","PROVEN"],["B - contactor opening <= 100 ms after injected isolation fault","Isolation-fault injection not executed","UNTESTED"]],[65*mm,75*mm,34*mm],y)
    note(c,"ANY_OF decision.","Path A is proven; therefore the alternative post-impact protection requirement is satisfied even though Path B remains untested.",y,"blue")
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",6,total,"TR-TH-04 thermal and coolant"); y=PAGE_H-47*mm; y=heading(c,"TR-TH-04 - Thermal protection and coolant pressure",y)
    y=draw_table(c,"Table 7. Thermal/coolant results",[["Test","Measured result","Recorded disposition"],["Thermal shutdown heating ramp","Shutdown asserted at 87 deg C","PASS for shutdown"],["Recovery below 70 deg C for 60 s","Cool-down phase not run","NOT TESTED"],["Coolant pressure hold","2.00 bar for 30.0 min","PASS"],["Visible leakage during hold","0.00 mL observed through chamber window","PASS in-run observation"]],[55*mm,75*mm,44*mm],y)
    figure(c,ASSETS/"thermal_shutdown_plot.png","Figure 3. Heating-phase trace. It proves shutdown behavior but contains no recovery evidence.",y,144*mm)
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",7,total,"TR-NW-05 communications and open work"); y=PAGE_H-47*mm; y=heading(c,"TR-NW-05 - CAN endurance and deferred drive-away test",y)
    y=draw_table(c,"Table 8. CAN endurance summary",[["Duration","Messages expected","Messages received","Maximum interval"],["30.0 min","18,000","18,000","95.1 ms"]],[38*mm,44*mm,44*mm,48*mm],y)
    y=figure(c,ASSETS/"can_heartbeat_plot.png","Figure 4. Heartbeat interval remained below 100 ms for the full 30-minute run; message counter showed zero loss.",y,148*mm)
    note(c,"Deferred test DT-17.","Drive-away inhibition with the charge connector engaged was not executed. No torque or movement measurement is available.",y,"amber")
    c.showPage()

    new_page(c,title,"AQL-DVTR-118 Rev A",8,total,"TR-MECH-06 ingress and instruments"); y=PAGE_H-47*mm; y=heading(c,"TR-MECH-06 - J17 immersion and instrument register",y)
    y=draw_table(c,"Table 9. J17 immersion record",[["Parameter","Recorded value","In-run result"],["Water depth","1.00 m","Meets method"],["Immersion duration","30.0 min","Meets method"],["Electrical continuity after immersion","Normal","PASS"],["External visual check before disassembly","No moisture visible","PASS"]],[62*mm,48*mm,64*mm],y)
    y=draw_table(c,"Table 10. Acceptance-data instruments",[["Serial","Use","Calibration date shown in report"],["DMM-22","Voltage measurements","2026-02-12"],["DAQ-14","Timing and CAN data","2026-01-29"],["HI-07","Isolation resistance","2025-05-03"]],[40*mm,79*mm,55*mm],y)
    note(c,"Inspection handoff.","Rear-boot internal moisture and calibration-certificate validity were evaluated after test in AQL-QIR-221. This page alone does not close those questions.",y,"amber")
    c.showPage(); c.save()


def make_inspection_pdf() -> None:
    path=DOCS/"03_Aquila_Qualification_Inspection_Dossier.pdf"; c=canvas.Canvas(str(path),pagesize=A4,pageCompression=1)
    total=5; title="Aquila Qualification & Inspection Dossier"
    new_page(c,title,"AQL-QIR-221 Rev B",1,total,"Record authority"); y=PAGE_H-48*mm; y=heading(c,"Post-test inspection and calibration review",y,22)
    y=paragraph(c,"Signed quality record for the same test article AQL-P3-017 after completion of AQL-DVTR-118. This dossier is authoritative for destructive internal inspection, photographed hardware observations and certificate validity.",y)
    y=draw_table(c,"Table 1. Record control",[["Field","Value"],["Document type","Quality inspection and qualification record"],["Inspector","R. Singh - signed 2026-07-21"],["Test article","AQL-P3-017"],["Related report","AQL-DVTR-118 Rev A"]],[53*mm,121*mm],y)
    note(c,"Interpretation.","A later direct internal inspection can contradict an earlier external or in-run observation when the two records address the same acceptance condition.",y,"blue")
    c.showPage()

    new_page(c,title,"AQL-QIR-221 Rev B",2,total,"Visual marking audit"); y=PAGE_H-47*mm; y=heading(c,"VI-02 - Service-disconnect marking photograph",y)
    y=figure(c,ASSETS/"service_disconnect_inspection.png","Figure 1. Photograph supplied by the inspection team: orange service-disconnect housing with yellow triangular black-lightning hazard symbol.",y,150*mm)
    y=note(c,"Traceability limitation.","The photograph contains no visible unit serial or station identifier. Only one cover is shown, while the requirement applies to every service-disconnect cover. Population coverage and identity of the photographed cover cannot be confirmed.",y,"amber")
    paragraph(c,"Inspection disposition: INCONCLUSIVE for full requirement scope. The visual design appears consistent, but this record cannot prove that all covers are marked before access.",y)
    c.showPage()

    new_page(c,title,"AQL-QIR-221 Rev B",3,total,"Coolant post-test inspection"); y=PAGE_H-47*mm; y=heading(c,"FI-03 - Coolant circuit close inspection",y)
    y=draw_table(c,"Table 2. Post-hold inspection findings",[["Location","Method","Observation","Disposition"],["Quick disconnect QD-1","White-cloth wipe","Dry","No finding"],["Quick disconnect QD-2","White-cloth wipe","Blue coolant residue; collected volume 0.4 mL","VISIBLE LEAKAGE"],["Manifold seam","UV lamp","Dry","No finding"]],[38*mm,45*mm,61*mm,30*mm],y)
    note(c,"Conflict with test report.","The chamber-window observation in AQL-DVTR-118 recorded 0.00 mL during the pressure hold. The later direct QD-2 inspection found visible coolant residue. For a no-visible-leakage condition, these are conflicting observations and require review.",y,"red")
    c.showPage()

    new_page(c,title,"AQL-QIR-221 Rev B",4,total,"J17 destructive ingress inspection"); y=PAGE_H-47*mm; y=heading(c,"IP-04 - Connector J17 rear-boot inspection",y)
    y=draw_table(c,"Table 3. Internal inspection after immersion",[["Step","Observation"],["Rear boot opened","Liquid droplets present behind boot seal"],["Collected liquid","0.6 mL, conductivity consistent with immersion bath"],["Contact pins","No corrosion at inspection time"],["Conclusion","Moisture ingress confirmed; IP67 no-ingress condition failed"]],[51*mm,123*mm],y)
    y=figure(c,ASSETS/"connector_ingress_diagram.png","Figure 2. Inspection cross-section showing the location of collected moisture behind the rear boot.",y,145*mm)
    note(c,"Direct contradiction.","AQL-DVTR-118 records an external visual PASS before disassembly. This destructive inspection directly observes internal moisture and therefore contradicts the no-ingress acceptance condition.",y,"red")
    c.showPage()

    new_page(c,title,"AQL-QIR-221 Rev B",5,total,"Calibration certificate review"); y=PAGE_H-47*mm; y=heading(c,"CAL-05 - Instrument certificate validation",y)
    y=draw_table(c,"Table 4. Certificate status on test date 2026-07-18",[["Serial","Certificate issue","Valid through","Age/status on test date"],["DMM-22","2026-02-12","2027-02-11","5 months - VALID"],["DAQ-14","2026-01-29","2027-01-28","6 months - VALID"],["HI-07","2025-05-03","2026-05-02","Expired 77 days before test - INVALID"]],[36*mm,46*mm,44*mm,48*mm],y)
    note(c,"Finding CAL-05-03.","HI-07 produced the acceptance isolation-resistance value but its calibration was not valid within 12 months on the test date. Instrument identity is traceable; calibration validity is not compliant.",y,"red")
    c.showPage(); c.save()


def ground_truth() -> dict:
    def ev(document, page, block_type, role, quote):
        return {"document": document, "page": page, "block_type": block_type, "role": role, "quote": quote}
    def cond(cid, desc, status, evidence, **fields):
        base={"condition_id":cid,"description":desc,"mandatory":True,"expected_status":status,"evidence":evidence}
        base.update(fields); return base
    test="02_Aquila_Design_Validation_Test_Report.pdf"; inspect="03_Aquila_Qualification_Inspection_Dossier.pdf"
    reqs=[]
    reqs.append({"requirement_id":"MM-REQ-001","clause":"MM-REQ-001","title":"Pack voltage monitoring","category":"Electrical","requirement_text":"The BMS shall continuously monitor pack voltage throughout 400 V to 800 V DC.","requirement_page":2,"logic":{"operator":"ALL_OF","condition_ids":["MM-001-C1","MM-001-C2"]},"expected_status":"SUPPORTED","expected_review_state":"Reviewed","conditions":[cond("MM-001-C1","Monitoring coverage includes a lower bound at or below 400 V","PROVEN",[ev(test,2,"table","supporting","380 V | 380.3 V | Below 400 V lower bound")],parameter="pack_voltage_lower_bound",operator="<=",threshold=400,unit="V"),cond("MM-001-C2","Monitoring coverage includes an upper bound at or above 800 V","PROVEN",[ev(test,2,"table","supporting","820 V | 819.6 V | Above 800 V upper bound")],parameter="pack_voltage_upper_bound",operator=">=",threshold=800,unit="V") ]})
    reqs.append({"requirement_id":"MM-REQ-002","clause":"MM-REQ-002","title":"Cell measurement accuracy","category":"Electrical","requirement_text":"Cell-voltage measurement error shall not exceed +/-5 mV from -20 deg C through +60 deg C.","requirement_page":2,"logic":{"operator":"ALL_OF","condition_ids":["MM-002-C1","MM-002-C2","MM-002-C3"]},"expected_status":"PARTIAL","expected_review_state":"Needs review","conditions":[cond("MM-002-C1","Absolute cell-voltage error does not exceed 5 mV","PROVEN",[ev(test,2,"table","supporting","+60 deg C | 4.8 mV | <= 5 mV")],parameter="cell_voltage_error",operator="<=",threshold=5,unit="mV"),cond("MM-002-C2","Accuracy verification includes -20 deg C","UNTESTED",[ev(test,2,"table","missing_marker","-20 deg C | Not measured | No result | Cold chamber unavailable")],parameter="temperature_lower_endpoint",operator="<=",threshold=-20,unit="deg C"),cond("MM-002-C3","Accuracy verification includes +60 deg C","PROVEN",[ev(test,2,"table","supporting","+60 deg C | 4.8 mV | Tested upper endpoint")],parameter="temperature_upper_endpoint",operator=">=",threshold=60,unit="deg C") ]})
    reqs.append({"requirement_id":"MM-REQ-003","clause":"MM-REQ-003","title":"Crash disconnect response","category":"Safety","requirement_text":"On a validated crash input, both main contactors shall open within 20 ms and the DC bus shall fall below 60 V within 5.0 s.","requirement_page":3,"logic":{"operator":"ALL_OF","condition_ids":["MM-003-C1","MM-003-C2"]},"expected_status":"SUPPORTED","expected_review_state":"Reviewed","conditions":[cond("MM-003-C1","Both main contactors open within 20 ms","PROVEN",[ev(test,3,"table","supporting","Both main contactors open | 14.6 ms | <= 20 ms | PASS"),ev(test,3,"figure","supporting","Contactors open: 14.6 ms")],parameter="contactor_open_latency",operator="<=",threshold=20,unit="ms",requires_visual_evidence=False),cond("MM-003-C2","DC bus falls below 60 V within 5.0 s","PROVEN",[ev(test,3,"table","supporting","DC bus at 4.20 s | 48 V | < 60 V by 5.0 s | PASS"),ev(test,3,"figure","supporting","48 V at 4.20 s")],parameter="dc_bus_voltage",operator="<",threshold=60,unit="V") ]})
    reqs.append({"requirement_id":"MM-REQ-004","clause":"MM-REQ-004","title":"Post-impact isolation","category":"Safety","requirement_text":"At 60 s after impact, measured isolation resistance divided by measured DC-bus voltage shall be at least 500 ohm/V.","requirement_page":3,"logic":{"operator":"ALL_OF","condition_ids":["MM-004-C1","MM-004-C2"]},"expected_status":"SUPPORTED","expected_review_state":"Reviewed","conditions":[cond("MM-004-C1","Isolation operands are measured at 60 s after impact","PROVEN",[ev(test,4,"table","supporting","Elapsed time after impact | t | 60.0 s | DAQ-14")],parameter="post_impact_dwell",operator=">=",threshold=60,unit="s"),cond("MM-004-C2","Calculated isolation ratio is at least 500 ohm/V","PROVEN",[ev(test,4,"table","operand","DC-bus voltage | Vbus | 402 V"),ev(test,4,"table","operand","Isolation resistance | Riso | 1.26 Mohm"),ev(test,5,"formula","supporting","Riso/Vbus = 1,260,000 ohm / 402 V = 3,134 ohm/V")],parameter="isolation_ratio",operator=">=",threshold=500,unit="ohm/V") ]})
    reqs.append({"requirement_id":"MM-REQ-005","clause":"MM-REQ-005","title":"Coolant circuit integrity","category":"Mechanical","requirement_text":"The coolant circuit shall hold 2.0 bar for 30 minutes with no visible leakage.","requirement_page":4,"logic":{"operator":"ALL_OF","condition_ids":["MM-005-C1","MM-005-C2"]},"expected_status":"CONFLICT","expected_review_state":"Needs review","conditions":[cond("MM-005-C1","Pressure is at least 2.0 bar for at least 30 minutes","PROVEN",[ev(test,6,"table","supporting","Coolant pressure hold | 2.00 bar for 30.0 min | PASS")],parameter="coolant_pressure_hold",operator=">=",threshold=2.0,unit="bar"),cond("MM-005-C2","No visible coolant leakage occurs","FAILED",[ev(test,6,"table","supporting","Visible leakage during hold | 0.00 mL observed through chamber window | PASS in-run observation"),ev(inspect,3,"table","contradicting","Quick disconnect QD-2 | White-cloth wipe | Blue coolant residue; collected volume 0.4 mL | VISIBLE LEAKAGE")],parameter="visible_coolant_leakage",operator="==",threshold=0,unit="mL") ]})
    reqs.append({"requirement_id":"MM-REQ-006","clause":"MM-REQ-006","title":"Service-disconnect hazard marking","category":"Visual inspection","requirement_text":"Every service-disconnect cover shall carry a readable yellow triangular warning label with a black lightning symbol before the cover can be accessed.","requirement_page":5,"logic":{"operator":"ALL_OF","condition_ids":["MM-006-C1","MM-006-C2"]},"expected_status":"UNKNOWN","expected_review_state":"Needs review","conditions":[cond("MM-006-C1","The mandated marking design is established for the controlled test article","INCONCLUSIVE",[ev(inspect,2,"figure","ambiguous","Photograph supplied by the inspection team: orange service-disconnect housing with yellow triangular black-lightning hazard symbol"),ev(inspect,2,"text","limitation","The photograph contains no visible unit serial or station identifier")],requires_visual_evidence=True,verification_method="visual"),cond("MM-006-C2","The marking is present and readable on every service-disconnect cover","INCONCLUSIVE",[ev(inspect,2,"text","limitation","Only one cover is shown, while the requirement applies to every service-disconnect cover")],requires_visual_evidence=True,verification_method="visual") ]})
    reqs.append({"requirement_id":"MM-REQ-007","clause":"MM-REQ-007","title":"Thermal shutdown and recovery","category":"Thermal","requirement_text":"The BMS shall command shutdown at or above 85 deg C and permit recovery only after temperature is below 70 deg C for 60 s.","requirement_page":4,"logic":{"operator":"ALL_OF","condition_ids":["MM-007-C1","MM-007-C2"]},"expected_status":"PARTIAL","expected_review_state":"Needs review","conditions":[cond("MM-007-C1","Shutdown is asserted at temperature at or above 85 deg C","PROVEN",[ev(test,6,"table","supporting","Thermal shutdown heating ramp | Shutdown asserted at 87 deg C | PASS for shutdown"),ev(test,6,"figure","supporting","Shutdown asserted at 87 C")],parameter="shutdown_temperature",operator=">=",threshold=85,unit="deg C"),cond("MM-007-C2","Recovery requires temperature below 70 deg C for 60 s","UNTESTED",[ev(test,6,"table","missing_marker","Recovery below 70 deg C for 60 s | Cool-down phase not run | NOT TESTED"),ev(test,6,"figure","missing_marker","No cool-down/recovery phase recorded")],parameter="recovery_dwell",operator=">=",threshold=60,unit="s") ]})
    reqs.append({"requirement_id":"MM-REQ-008","clause":"MM-REQ-008","title":"CAN heartbeat endurance","category":"Communications","requirement_text":"The BMS CAN heartbeat interval shall not exceed 100 ms during a continuous 30-minute run, with zero lost heartbeat messages.","requirement_page":5,"logic":{"operator":"ALL_OF","condition_ids":["MM-008-C1","MM-008-C2"]},"expected_status":"SUPPORTED","expected_review_state":"Reviewed","conditions":[cond("MM-008-C1","Maximum heartbeat interval does not exceed 100 ms","PROVEN",[ev(test,7,"table","supporting","30.0 min | 18,000 | 18,000 | 95.1 ms"),ev(test,7,"figure","supporting","Heartbeat interval remained below 100 ms")],parameter="heartbeat_interval",operator="<=",threshold=100,unit="ms"),cond("MM-008-C2","Zero heartbeat messages are lost during at least 30 minutes","PROVEN",[ev(test,7,"table","supporting","Duration 30.0 min | Messages expected 18,000 | Messages received 18,000")],parameter="lost_heartbeat_messages",operator="==",threshold=0,unit="count") ]})
    reqs.append({"requirement_id":"MM-REQ-009","clause":"MM-REQ-009","title":"Connector J17 ingress protection","category":"Environmental","requirement_text":"Connector J17 shall remain IP67 after 1 m water immersion for 30 minutes, with no moisture inside the rear boot.","requirement_page":6,"logic":{"operator":"ALL_OF","condition_ids":["MM-009-C1","MM-009-C2"]},"expected_status":"CONFLICT","expected_review_state":"Needs review","conditions":[cond("MM-009-C1","Immersion depth is at least 1 m for at least 30 minutes","PROVEN",[ev(test,8,"table","supporting","Water depth | 1.00 m"),ev(test,8,"table","supporting","Immersion duration | 30.0 min")],parameter="immersion_depth",operator=">=",threshold=1,unit="m"),cond("MM-009-C2","No moisture is present inside the J17 rear boot","FAILED",[ev(test,8,"table","supporting","External visual check before disassembly | No moisture visible | PASS"),ev(inspect,4,"table","contradicting","Collected liquid | 0.6 mL, conductivity consistent with immersion bath"),ev(inspect,4,"figure","contradicting","Blue marker indicates collected liquid behind the rear boot")],parameter="internal_moisture",operator="==",threshold=0,unit="mL",requires_visual_evidence=False) ]})
    reqs.append({"requirement_id":"MM-REQ-010","clause":"MM-REQ-010","title":"Instrument traceability","category":"Quality","requirement_text":"Every measuring instrument used for acceptance data shall be identified by serial number and have calibration valid within 12 months on the test date.","requirement_page":6,"logic":{"operator":"ALL_OF","condition_ids":["MM-010-C1","MM-010-C2"]},"expected_status":"CONFLICT","expected_review_state":"Needs review","conditions":[cond("MM-010-C1","Each acceptance measurement identifies an instrument serial number","PROVEN",[ev(test,8,"table","supporting","Acceptance-data instruments | DMM-22 | DAQ-14 | HI-07")],verification_method="record_review"),cond("MM-010-C2","Each identified instrument has calibration valid within 12 months on the test date","FAILED",[ev(inspect,5,"table","contradicting","HI-07 | 2025-05-03 | 2026-05-02 | Expired 77 days before test - INVALID")],parameter="calibration_age",operator="<=",threshold=12,unit="months") ]})
    reqs.append({"requirement_id":"MM-REQ-011","clause":"MM-REQ-011","title":"Drive-away inhibition","category":"Functional safety","requirement_text":"If the charge connector is engaged, commanded propulsion torque shall remain zero and vehicle movement shall not exceed 150 mm.","requirement_page":7,"logic":{"operator":"IF_THEN","if_condition_id":"MM-011-C1","then_condition_ids":["MM-011-C2"]},"expected_status":"MISSING","expected_review_state":"Needs review","conditions":[cond("MM-011-C1","With charge connector engaged, commanded propulsion torque remains 0 Nm","UNTESTED",[ev(test,7,"text","missing_marker","Drive-away inhibition with the charge connector engaged was not executed. No torque or movement measurement is available")],parameter="propulsion_torque",operator="==",threshold=0,unit="Nm"),cond("MM-011-C2","With charge connector engaged, vehicle movement does not exceed 150 mm","UNTESTED",[ev(test,7,"text","missing_marker","No torque or movement measurement is available")],parameter="vehicle_movement",operator="<=",threshold=150,unit="mm") ]})
    reqs.append({"requirement_id":"MM-REQ-012","clause":"MM-REQ-012","title":"Alternative post-impact electrical protection","category":"Safety logic","requirement_text":"After frontal impact, electrical protection is acceptable when either the isolation ratio is at least 500 ohm/V or an isolation fault causes the main contactors to open within 100 ms.","requirement_page":7,"logic":{"operator":"ANY_OF","condition_ids":["MM-012-C1","MM-012-C2"]},"expected_status":"SUPPORTED","expected_review_state":"Reviewed","conditions":[cond("MM-012-C1","Post-impact isolation ratio is at least 500 ohm/V","PROVEN",[ev(test,5,"formula","supporting","Riso/Vbus = 1,260,000 ohm / 402 V = 3,134 ohm/V")],parameter="isolation_ratio",operator=">=",threshold=500,unit="ohm/V"),cond("MM-012-C2","Isolation-fault contactor opening latency does not exceed 100 ms","UNTESTED",[ev(test,5,"table","missing_marker","B - contactor opening <= 100 ms after injected isolation fault | Isolation-fault injection not executed | UNTESTED")],parameter="isolation_fault_open_latency",operator="<=",threshold=100,unit="ms") ]})
    return {"benchmark_id":"traceaudit-multimodal-diagnostic","version":"1.0.0","description":"Synthetic document-understanding benchmark with tables, figures, cross-page calculations, conflicts and explicit missing evidence.","documents":{"requirements":{"filename":"01_Aquila_BMS_System_Requirements.pdf","doc_type":"System specification","expected_role":"SPECIFICATION","expected_verification_basis":"normative"},"evidence":[{"filename":test,"doc_type":"Test report","expected_role":"TEST_REPORT","expected_verification_basis":"physical_test"},{"filename":inspect,"doc_type":"Quality inspection record","expected_role":"OTHER_EVIDENCE","expected_verification_basis":"inspection"}]},"requirements":reqs}


def write_metadata() -> None:
    dataset=ground_truth()
    (ROOT/"ground_truth.json").write_text(json.dumps(dataset,indent=2),encoding="utf-8")
    manifest={"benchmark_id":dataset["benchmark_id"],"version":dataset["version"],"document_count":3,"requirement_count":len(dataset["requirements"]),"atomic_condition_count":sum(len(r["conditions"]) for r in dataset["requirements"]),"expected_distribution":{status:sum(r["expected_status"]==status for r in dataset["requirements"]) for status in ["SUPPORTED","PARTIAL","CONFLICT","MISSING","UNKNOWN"]},"visual_assets":["system_architecture.png","crash_timing_plot.png","thermal_shutdown_plot.png","can_heartbeat_plot.png","service_disconnect_inspection.png","connector_ingress_diagram.png"],"design_principles":["Ground truth is evaluator-only","Every condition has document/page/block provenance","Cross-page and cross-document evidence are intentional","Figure-only scope claims remain inconclusive","Later direct inspections can contradict in-run observations"]}
    (ROOT/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")


def main() -> None:
    DOCS.mkdir(parents=True,exist_ok=True); ASSETS.mkdir(parents=True,exist_ok=True)
    photo=ASSETS/"service_disconnect_inspection.png"
    if not photo.exists():
        raise FileNotFoundError(f"Required generated inspection photograph is missing: {photo}")
    build_visual_assets(); make_requirements_pdf(); make_test_report_pdf(); make_inspection_pdf(); write_metadata()
    print("Generated multimodal benchmark:")
    for path in sorted(DOCS.glob("*.pdf")): print(f"  {path.name} ({path.stat().st_size:,} bytes)")
    print("  ground_truth.json")
    print("  manifest.json")


if __name__ == "__main__":
    main()
