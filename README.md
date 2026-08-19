# TraceAudit AI

Build a polished, production-quality SaaS web application called TraceAudit AI.

1. Product concept

TraceAudit AI is an AI-assisted technical requirements and documentation auditing platform for product manufacturers and engineering teams.

The platform helps engineering, quality, validation, and product-compliance teams analyze a set of technical requirements and supporting documents.

It should:

ingest technical documents

identify and structure requirements

find supporting evidence across documents

detect missing evidence

detect partial evidence

detect potential contradictions

create requirement-to-evidence traceability

highlight findings requiring human review

generate an auditable report

IMPORTANT PRODUCT POSITIONING:

The application is decision-support software, not a legal certification tool.

Do NOT use language such as:

"Your product is legally compliant"

"Certified compliant"

"Automatically certifies your product"

"Guaranteed EU compliance"

Instead use terminology such as:

"AI-assisted audit"

"Evidence coverage"

"Requirement traceability"

"Potential compliance gaps"

"Potential inconsistencies"

"Human review required"

"Technical documentation analysis"

The product should feel like a serious enterprise engineering platform rather than a generic AI chatbot.

2. Target users

Primary users:

Product Compliance Engineers

Quality Engineers

Systems Engineers

Requirements Engineers

Validation/Test Engineers

Engineering Managers

Technical Documentation teams

Initial vertical:

Industrial / electronic product manufacturers

The UI should make the product feel immediately relevant to companies that work with:

product requirements

technical specifications

test reports

risk assessments

supplier documentation

engineering standards

technical files

3. Design direction

Create a premium B2B SaaS interface.

Visual personality:

sophisticated

technical

trustworthy

minimal

modern

enterprise-grade

data-driven

calm

precise

Think:

Linear + Vercel + modern enterprise engineering software

Avoid:

excessive gradients

flashy AI visuals

cartoon illustrations

huge rounded cards everywhere

generic chatbot aesthetics

excessive glassmorphism

purple AI clichés

Use a primarily light interface with subtle neutral backgrounds.

Suggested visual system:

white / off-white page backgrounds

dark navy / charcoal typography

subtle borders

restrained blue accent

green for positive findings

amber for warnings

red for critical findings

gray for neutral states

Use color meaningfully rather than decoratively.

4. Application shell

Create a persistent application layout.

Left sidebar

Logo:

TraceAudit

small label:

AI Technical Audit

Navigation:

Dashboard

Projects

Documents

Requirements

Findings

Traceability

Reports

Settings

At the bottom:

Help

User profile

"Hamza Meskini"

AI Engineer / Admin

Sidebar should be collapsible.

5. Top navigation

Top bar should contain:

Left:

breadcrumb / current page

Center or right:

project selector

Example:

Industrial Controller X200

Right:

search

notifications

help

user avatar

6. Dashboard

Create a beautiful executive dashboard.

Page title:

Audit Overview

Subtitle:

Monitor requirements coverage, evidence quality, and unresolved findings.

Top-level project selector:

Industrial Controller X200

Status:

Analysis completed

Show:

KPI cards

Requirements

347

"Total requirements analyzed"

Evidence Coverage

82%

"284 requirements fully supported"

Open Findings

63

"18 missing · 31 partial · 14 conflicts"

Documents

17

"1,284 evidence segments indexed"

7. Main dashboard visualization

Create a large card:

Requirement Coverage

Donut / circular visualization.

Segments:

Supported: 284

Partial: 31

Missing: 18

Conflict: 14

Center:

82%

Label:

Fully supported

Use accessible color semantics.

Under the chart show a legend.

8. Findings overview

Create a large card:

Findings requiring attention

Display:

Critical — 3

High — 8

Medium — 23

Low — 29

Use a horizontal severity visualization.

Add button:

View all findings

9. Recent findings

Table:

Columns:

ID

Requirement

Type

Severity

Status

Evidence

Last updated

Example rows:

REQ-184
Operating voltage requirement
Potential conflict
High
Needs review
3 sources
2h ago

REQ-221
Over-voltage protection
Missing evidence
Critical
Open
0 sources
3h ago

REQ-109
Operating temperature
Partial evidence
Medium
Needs review
2 sources
5h ago

REQ-301
Safety documentation
Missing evidence
High
Open
0 sources
Yesterday

Rows should be clickable.

10. Quick actions

A prominent card:

Start a new audit

Text:

"Analyze requirements and supporting technical documentation."

Buttons:

New Audit

Upload Documents

Import Requirements

11. Project page

Create a dedicated project page.

Header:

Industrial Controller X200

Metadata:

Project ID: TA-2026-0042

Product category: Industrial electronic controller

Created: August 10, 2026

Last analysis: August 17, 2026

Status: Analysis complete

Tabs:

Overview

Requirements

Documents

Findings

Traceability

Reports

12. Project overview

Show:

Audit progress

100%

Documents:

17

Requirements:

347

Evidence links:

612

Findings:

63

Human reviews completed:

41

13. Documents page

Create a document-management interface.

Page title:

Technical Documents

Subtitle:

"Manage the documents used as evidence for this audit."

Button:

Upload documents

Document table:

Columns:

Document

Type

Version

Pages

Requirements linked

Processing status

Updated

Use realistic mock documents:

Product_Specification_X200.pdf
Technical specification
v2.4
48 pages
126 requirements

Safety_Test_Report.pdf
Test report
v1.8
73 pages
84 requirements

Risk_Assessment_X200.xlsx
Risk assessment
v3.1
—
57 requirements

Supplier_Datasheet_MainController.pdf
Supplier documentation
v4.0
18 pages
43 requirements

Environmental_Test_Report.pdf
Test report
v2.2
41 pages
72 requirements

User_Manual_X200.docx
Technical documentation
v5.0
62 pages
39 requirements

14. Document upload experience

Create a drag-and-drop upload component.

Headline:

Upload technical documentation

Supported file types:

PDF, DOCX, XLSX, CSV

Show upload stages:

Upload

Extract

Analyze

Index evidence

Show sample progress state:

"Processing Technical_Specification.pdf"

87%

"Extracting tables and structured requirements..."

After completion:

17 documents processed successfully

15. Requirements page

Page title:

Requirements

Subtitle:

"Review extracted requirements and their evidence coverage."

Top filters:

All

Supported

Partial

Missing

Conflict

Needs review

Search bar:

"Search requirements..."

Filter controls:

Category

Severity

Source document

Status

Table columns:

ID

Requirement

Category

Evidence

Status

Confidence

Review

Example:

REQ-001
Operating voltage must remain within 18–32 V DC
Electrical
3 sources
Supported
98%
Reviewed

REQ-002
Device shall provide over-voltage protection
Safety
2 sources
Supported
95%
Reviewed

REQ-003
Device shall operate from -20°C to +70°C
Environmental
2 sources
Partial
87%
Needs review

REQ-004
Manufacturer shall document identified product risks
Safety
0 sources
Missing
94%
Open

REQ-005
Controller input voltage tolerance shall comply with supplier specification
Electrical
3 sources
Conflict
92%
Needs review

16. Requirement detail page

This should be one of the most impressive parts of the application.

When the user clicks REQ-005, show a split-screen workspace.

Left side:

Requirement

REQ-005

Title:

Controller input voltage tolerance shall comply with supplier specification

Category:

Electrical

Severity:

High

Status:

Potential conflict

Confidence:

92%

Right side:

Evidence analysis

Show evidence cards.

Product Specification

Document:

Product_Specification_X200.pdf

Page 12

Quote:

"Operating input voltage: 18–32 V DC."

Status:

Supports requirement

Supplier Datasheet

Document:

Supplier_Datasheet_MainController.pdf

Page 4

Quote:

"Recommended input voltage range: 18–30 V DC."

Status:

Potential conflict

Highlight the conflicting values.

Test Report

Document:

Environmental_Test_Report.pdf

Page 19

Quote:

"The controller successfully operated at 18 V, 24 V and 32 V."

Status:

Supporting evidence

17. AI analysis panel

Add a dedicated panel titled:

AI Analysis

Text:

"The available evidence indicates a potential discrepancy between the product specification and supplier documentation. The product specification allows operation up to 32 V, while the supplier datasheet specifies a maximum recommended input voltage of 30 V."

Then:

Recommended action

"Review the supplier specification and confirm the permitted operating range before final approval."

Important:

Do not say:

"This product is non-compliant."

Use:

"Potential conflict detected."

18. Human review controls

At the bottom of the requirement page:

Buttons:

Approve finding

Reject finding

Mark as reviewed

Add comment

Request clarification

Include a comment box.

Example mock comment:

"Supplier confirmed that 32 V is acceptable under transient conditions. Engineering review required."

19. Findings page

Create a dedicated findings management workspace.

Page title:

Findings

Summary cards:

63 Total

3 Critical

8 High

23 Medium

29 Low

Filters:

Severity

Type

Status

Category

Reviewer

Finding types:

Missing evidence

Partial evidence

Potential conflict

Unsupported requirement

Duplicate requirement

Ambiguous requirement

Table:

ID
Type
Requirement
Severity
Evidence
Status
Owner
Updated

Example:

F-001
Potential conflict
REQ-005
High
3 sources
Needs review
A. Benali
2 hours ago

F-002
Missing evidence
REQ-004
Critical
0 sources
Open
Unassigned
3 hours ago

20. Traceability page

This should demonstrate one of the key strengths of the product.

Page title:

Traceability Map

Create an interactive graph.

Nodes:

Requirements
Documents
Evidence
Findings

Example relationships:

REQ-001
↓
Product_Spec.pdf
↓
Page 12

REQ-003
↓
Technical_Spec.pdf
↓
Page 31

REQ-005
↓
Product_Spec.pdf
↓
Supplier_Datasheet.pdf
↓
Finding F-001

Use a clean network visualization.

Allow:

zoom

pan

click node

highlight connections

filter node type

Add a legend.

21. Reports page

Page title:

Audit Reports

Show generated reports:

Technical Evidence Audit
Industrial Controller X200
Generated Aug 17, 2026

Sections:

Executive summary

Requirement coverage

Missing evidence

Potential conflicts

Traceability

Human review status

Buttons:

Preview

Export PDF

Export CSV

22. Executive report preview

Create an extremely polished report-style screen.

Header:

Technical Evidence Audit

Industrial Controller X200

Audit ID:

TA-2026-0042

Analysis date:

17 August 2026

Summary:

Requirements analyzed:

347

Fully supported:

284

Partial:

31

Missing:

18

Potential conflicts:

14

Overall evidence coverage:

82%

Use a professional visualization.

Then sections:

Key observations

82% of requirements have complete supporting evidence.

18 requirements currently have no identified evidence.

14 potential inconsistencies were detected across technical documents.

23 findings require human review.

Add an explicit disclaimer:

"This report provides AI-assisted technical documentation analysis and evidence traceability. It does not constitute legal advice, certification, or a determination of regulatory conformity. Final assessment remains the responsibility of the manufacturer and relevant qualified professionals."

23. Regulatory / requirement framework page

Create a page called:

Requirement Frameworks

This allows users to select what they want to evaluate against.

Cards:

Company Requirements

Status:
Active

347 requirements

Product Safety Checklist

Status:
Active

128 requirements

Cybersecurity Requirements

Status:
Available

96 requirements

EU Product Requirements

Status:
Configuration required

Do NOT make claims that these automatically determine legal conformity.

For the mock interface, show:

Selected framework

"EU Product Requirements — Example Dataset"

Description:

"Example requirement set used for technical documentation analysis."

Include:

Configure framework

24. Audit creation wizard

Create a polished 4-step wizard.

Step 1 — Project

Project name

Product name

Product category

Step 2 — Requirements

Choose:

Upload requirements

or

Select framework

Step 3 — Evidence

Upload:

Technical specifications
Test reports
Risk assessments
Supplier documents
Other evidence

Step 4 — Review

Summary:

Requirements:

347

Documents:

17

Framework:

EU Product Requirements — Example Dataset

Button:

Start AI Audit

Show progress afterwards.

25. Settings

Create enterprise-style settings.

Sections:

Organization

Users & roles

Projects

AI configuration

Document retention

Audit logs

Security

Roles:

Admin
Reviewer
Engineer
Viewer

26. AI activity indicator

Throughout the application, use a subtle AI status indicator.

Example:

AI analysis complete

or

Analyzing evidence...

But avoid constantly showing flashy "AI magic" animations.

This is engineering software, not an AI toy.

27. Empty states

Design excellent empty states.

Example:

"No audits yet."

"Create your first technical documentation audit to begin."

Button:

Create audit

Another:

"No findings require your attention."

"Your current audit has no unresolved findings."

28. Notifications

Create realistic notifications:

"14 potential conflicts detected."

"Technical_Specification.pdf finished processing."

"REQ-004 requires human review."

"Audit report generated."

29. Search

Global search should search:

requirements

documents

findings

projects

Example:

Search:

"voltage"

Results:

REQ-001
REQ-005
Product_Specification.pdf
Supplier_Datasheet.pdf
Finding F-001

30. Mock data

Use realistic industrial mock data.

Product:

Industrial Controller X200

Company:

Atlas Motion Systems

Project:

X200 EU Technical Documentation Audit

Requirements:

347

Documents:

17

Evidence segments:

1,284

Findings:

63

Evidence coverage:

82%

Example categories:

Electrical

Safety

Environmental

Mechanical

Cybersecurity

Documentation

Use realistic requirement IDs:

REQ-001
REQ-002
REQ-003
...

Findings:

F-001
F-002
F-003
...

Documents should have realistic names.

31. Important interaction details

Make the prototype feel functional even though everything is mock data.

Implement:

sidebar navigation

page routing

project selection

search

filters

sorting

tabs

dropdowns

upload UI

audit wizard

requirement detail pages

finding detail pages

traceability visualization

report preview

status changes

approve/reject actions

comments

notification dropdown

Actions should update the frontend state so the demo feels like a real product.

Use local mock data / local state.

No backend required yet.

32. Technical implementation

Use:

React

TypeScript

Tailwind CSS

shadcn/ui

Lucide icons

Recharts where appropriate

Keep components modular.

Create reusable:

KPI cards

data tables

status badges

severity badges

document cards

evidence cards

finding cards

modal dialogs

audit wizard

tabs

filters

Use TypeScript interfaces for:

Project
Requirement
Document
Evidence
Finding
Framework
Audit
User

33. Responsive design

Desktop is the primary experience.

Also ensure:

tablet support

reasonable mobile fallback

For complex tables and traceability graphs, prioritize desktop usability.

34. Performance / UX

The UI should feel fast.

Use:

skeleton loading states

subtle transitions

optimistic UI for review actions

pagination for large tables

lazy rendering for large graph views

Avoid unnecessary animations.

35. Visual details that make it feel premium

Use:

10–12px corner radius rather than huge rounded cards

very subtle shadows

1px borders

compact professional spacing

excellent typography hierarchy

dense but readable tables

sticky table headers

hover states

command/search interface

keyboard-friendly interactions

clear status semantics

Do not make everything a card.

Use whitespace and hierarchy.

36. Landing/dashboard feeling

The first screen should immediately communicate:

What does this product do?

A concise banner near the top:

Turn technical documentation into traceable evidence.

Subheading:

Analyze requirements, technical documents, and test evidence to identify coverage gaps, inconsistencies, and items requiring human review.

Primary button:

Start an audit

Secondary:

View demo audit

37. Product terminology

Use these terms consistently:

Requirement
Evidence
Source
Finding
Traceability
Coverage
Potential conflict
Missing evidence
Partial evidence
Human review
Audit
Framework
Document
Reviewer

Avoid repeatedly saying:

AI-powered
GenAI
LLM
Agent

The technology is behind the product.

The user cares about the audit.

38. Demo experience

Make the demo deliberately impressive.

The default project should be fully populated.

The user can click:

Dashboard
→ Requirement coverage
→ REQ-005
→ Evidence
→ Supplier document
→ potential conflict
→ traceability relationship
→ finding
→ review it
→ generate report

The demo should tell a coherent story.

39. Final objective

At the end of the implementation, I should be able to open the application and understand within 30 seconds:

What the product does

Who it's for

What has been analyzed

Where problems exist

Why the results are trustworthy

How evidence is traced back to source documents

How a human reviews the AI findings

How an audit report is generated

The interface should feel credible enough to show to:

engineering managers

product compliance engineers

quality managers

startup founders

potential customers

investors

Build the entire experience using mock data first. Do not build a backend, authentication system, real LLM integration, real document processing pipeline, or payment system yet.

The goal of this iteration is an exceptionally polished, believable, interactive product prototype that lets us validate the UX and explain the business concept to potential customers.

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/dc58c1b8-4e62-4938-ab0e-8bc575903cba).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
