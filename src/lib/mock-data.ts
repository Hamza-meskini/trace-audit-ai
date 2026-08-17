export type CoverageStatus = "Supported" | "Partial" | "Missing" | "Conflict";
export type ReviewState = "Reviewed" | "Needs review" | "Open" | "Approved" | "Rejected";
export type Severity = "Critical" | "High" | "Medium" | "Low";
export type FindingType =
  | "Missing evidence"
  | "Partial evidence"
  | "Potential conflict"
  | "Unsupported requirement"
  | "Duplicate requirement"
  | "Ambiguous requirement";
export type Category =
  | "Electrical"
  | "Safety"
  | "Environmental"
  | "Mechanical"
  | "Cybersecurity"
  | "Documentation";

export interface User {
  id: string;
  name: string;
  role: "Admin" | "Reviewer" | "Engineer" | "Viewer";
  title: string;
  initials: string;
}

export interface Project {
  id: string;
  name: string;
  auditId: string;
  company: string;
  productCategory: string;
  created: string;
  lastAnalysis: string;
  status: string;
}

export interface TechDocument {
  id: string;
  name: string;
  type: string;
  version: string;
  pages: string;
  requirementsLinked: number;
  processing: "Indexed" | "Processing" | "Queued";
  updated: string;
}

export interface Evidence {
  id: string;
  document: string;
  page: number;
  quote: string;
  status: "Supports requirement" | "Potential conflict" | "Supporting evidence";
  label: string;
  highlight?: string;
}

export interface Requirement {
  id: string;
  title: string;
  category: Category;
  sources: number;
  status: CoverageStatus;
  confidence: number;
  review: ReviewState;
  severity: Severity;
  sourceDocument: string;
  evidence: Evidence[];
  analysis?: string;
  recommendation?: string;
}

export interface Finding {
  id: string;
  type: FindingType;
  requirement: string;
  requirementTitle: string;
  severity: Severity;
  sources: number;
  status: ReviewState;
  owner: string;
  updated: string;
  category: Category;
}

export interface Framework {
  id: string;
  name: string;
  status: "Active" | "Available" | "Configuration required";
  requirements: number | null;
  description: string;
}

export const currentUser: User = {
  id: "u-1",
  name: "Hamza Meskini",
  role: "Admin",
  title: "AI Engineer / Admin",
  initials: "HM",
};

export const projects: Project[] = [
  {
    id: "TA-2026-0042",
    name: "Industrial Controller X200",
    auditId: "TA-2026-0042",
    company: "Atlas Motion Systems",
    productCategory: "Industrial electronic controller",
    created: "August 10, 2026",
    lastAnalysis: "August 17, 2026",
    status: "Analysis complete",
  },
  {
    id: "TA-2026-0039",
    name: "Servo Drive S80",
    auditId: "TA-2026-0039",
    company: "Atlas Motion Systems",
    productCategory: "Motion drive electronics",
    created: "July 22, 2026",
    lastAnalysis: "August 12, 2026",
    status: "Analysis complete",
  },
  {
    id: "TA-2026-0051",
    name: "Sensor Hub H12",
    auditId: "TA-2026-0051",
    company: "Atlas Motion Systems",
    productCategory: "Industrial sensor gateway",
    created: "August 15, 2026",
    lastAnalysis: "In progress",
    status: "Analyzing evidence",
  },
];

export const projectStats = {
  requirements: 347,
  coverage: 82,
  supported: 284,
  partial: 31,
  missing: 18,
  conflict: 14,
  documents: 17,
  evidenceSegments: 1284,
  evidenceLinks: 612,
  findings: 63,
  humanReviews: 41,
  progress: 100,
};

export const severityCounts = {
  Critical: 3,
  High: 8,
  Medium: 23,
  Low: 29,
} as const;

export const documents: TechDocument[] = [
  {
    id: "DOC-01",
    name: "Product_Specification_X200.pdf",
    type: "Technical specification",
    version: "v2.4",
    pages: "48 pages",
    requirementsLinked: 126,
    processing: "Indexed",
    updated: "2h ago",
  },
  {
    id: "DOC-02",
    name: "Safety_Test_Report.pdf",
    type: "Test report",
    version: "v1.8",
    pages: "73 pages",
    requirementsLinked: 84,
    processing: "Indexed",
    updated: "5h ago",
  },
  {
    id: "DOC-03",
    name: "Risk_Assessment_X200.xlsx",
    type: "Risk assessment",
    version: "v3.1",
    pages: "—",
    requirementsLinked: 57,
    processing: "Indexed",
    updated: "Yesterday",
  },
  {
    id: "DOC-04",
    name: "Supplier_Datasheet_MainController.pdf",
    type: "Supplier documentation",
    version: "v4.0",
    pages: "18 pages",
    requirementsLinked: 43,
    processing: "Indexed",
    updated: "Yesterday",
  },
  {
    id: "DOC-05",
    name: "Environmental_Test_Report.pdf",
    type: "Test report",
    version: "v2.2",
    pages: "41 pages",
    requirementsLinked: 72,
    processing: "Indexed",
    updated: "2 days ago",
  },
  {
    id: "DOC-06",
    name: "User_Manual_X200.docx",
    type: "Technical documentation",
    version: "v5.0",
    pages: "62 pages",
    requirementsLinked: 39,
    processing: "Processing",
    updated: "3 days ago",
  },
];

export const requirements: Requirement[] = [
  {
    id: "REQ-001",
    title: "Operating voltage must remain within 18–32 V DC",
    category: "Electrical",
    sources: 3,
    status: "Supported",
    confidence: 98,
    review: "Reviewed",
    severity: "Medium",
    sourceDocument: "Product_Specification_X200.pdf",
    evidence: [
      {
        id: "EV-1",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 12,
        quote: "Operating input voltage: 18–32 V DC.",
        status: "Supports requirement",
      },
      {
        id: "EV-2",
        label: "Test Report",
        document: "Environmental_Test_Report.pdf",
        page: 19,
        quote: "The controller successfully operated at 18 V, 24 V and 32 V.",
        status: "Supporting evidence",
      },
    ],
  },
  {
    id: "REQ-002",
    title: "Device shall provide over-voltage protection",
    category: "Safety",
    sources: 2,
    status: "Supported",
    confidence: 95,
    review: "Reviewed",
    severity: "High",
    sourceDocument: "Safety_Test_Report.pdf",
    evidence: [
      {
        id: "EV-3",
        label: "Safety Test Report",
        document: "Safety_Test_Report.pdf",
        page: 34,
        quote: "Over-voltage clamping verified at 36 V with no functional degradation.",
        status: "Supports requirement",
      },
    ],
  },
  {
    id: "REQ-003",
    title: "Device shall operate from -20°C to +70°C",
    category: "Environmental",
    sources: 2,
    status: "Partial",
    confidence: 87,
    review: "Needs review",
    severity: "Medium",
    sourceDocument: "Environmental_Test_Report.pdf",
    evidence: [
      {
        id: "EV-4",
        label: "Environmental Test Report",
        document: "Environmental_Test_Report.pdf",
        page: 31,
        quote: "Thermal cycling performed between -20°C and +60°C.",
        status: "Potential conflict",
        highlight: "+60°C",
      },
      {
        id: "EV-5",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 8,
        quote: "Ambient operating temperature: -20°C to +70°C.",
        status: "Supports requirement",
      },
    ],
    analysis:
      "Test evidence covers only part of the declared temperature range. No test record was identified above +60°C.",
    recommendation:
      "Extend environmental testing to +70°C or align the declared operating range with available test evidence.",
  },
  {
    id: "REQ-004",
    title: "Manufacturer shall document identified product risks",
    category: "Safety",
    sources: 0,
    status: "Missing",
    confidence: 94,
    review: "Open",
    severity: "Critical",
    sourceDocument: "Risk_Assessment_X200.xlsx",
    evidence: [],
    analysis: "No evidence segment in the indexed document set addresses this requirement.",
    recommendation: "Upload the signed risk assessment record covering identified product risks.",
  },
  {
    id: "REQ-005",
    title: "Controller input voltage tolerance shall comply with supplier specification",
    category: "Electrical",
    sources: 3,
    status: "Conflict",
    confidence: 92,
    review: "Needs review",
    severity: "High",
    sourceDocument: "Supplier_Datasheet_MainController.pdf",
    evidence: [
      {
        id: "EV-6",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 12,
        quote: "Operating input voltage: 18–32 V DC.",
        status: "Supports requirement",
        highlight: "32 V",
      },
      {
        id: "EV-7",
        label: "Supplier Datasheet",
        document: "Supplier_Datasheet_MainController.pdf",
        page: 4,
        quote: "Recommended input voltage range: 18–30 V DC.",
        status: "Potential conflict",
        highlight: "30 V",
      },
      {
        id: "EV-8",
        label: "Test Report",
        document: "Environmental_Test_Report.pdf",
        page: 19,
        quote: "The controller successfully operated at 18 V, 24 V and 32 V.",
        status: "Supporting evidence",
      },
    ],
    analysis:
      "The available evidence indicates a potential discrepancy between the product specification and supplier documentation. The product specification allows operation up to 32 V, while the supplier datasheet specifies a maximum recommended input voltage of 30 V.",
    recommendation:
      "Review the supplier specification and confirm the permitted operating range before final approval.",
  },
  {
    id: "REQ-006",
    title: "Enclosure shall provide IP54 ingress protection",
    category: "Mechanical",
    sources: 2,
    status: "Supported",
    confidence: 96,
    review: "Reviewed",
    severity: "Low",
    sourceDocument: "Product_Specification_X200.pdf",
    evidence: [
      {
        id: "EV-9",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 21,
        quote: "Enclosure rating: IP54 per housing qualification test plan.",
        status: "Supports requirement",
      },
    ],
  },
  {
    id: "REQ-007",
    title: "Firmware update packages shall be cryptographically signed",
    category: "Cybersecurity",
    sources: 1,
    status: "Partial",
    confidence: 81,
    review: "Needs review",
    severity: "High",
    sourceDocument: "Product_Specification_X200.pdf",
    evidence: [
      {
        id: "EV-10",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 39,
        quote: "Firmware images are validated using a vendor signature check at boot.",
        status: "Supporting evidence",
      },
    ],
    analysis: "Signature verification is described, but no key management evidence was identified.",
    recommendation: "Provide key management and signing process documentation.",
  },
  {
    id: "REQ-008",
    title: "User manual shall include installation safety instructions",
    category: "Documentation",
    sources: 1,
    status: "Supported",
    confidence: 93,
    review: "Reviewed",
    severity: "Low",
    sourceDocument: "User_Manual_X200.docx",
    evidence: [
      {
        id: "EV-11",
        label: "User Manual",
        document: "User_Manual_X200.docx",
        page: 6,
        quote: "Section 2 – Installation safety: disconnect supply before wiring.",
        status: "Supports requirement",
      },
    ],
  },
  {
    id: "REQ-009",
    title: "Product shall withstand 4 kV surge on power inputs",
    category: "Electrical",
    sources: 0,
    status: "Missing",
    confidence: 90,
    review: "Open",
    severity: "High",
    sourceDocument: "Safety_Test_Report.pdf",
    evidence: [],
    analysis: "No surge immunity test record was identified in the indexed document set.",
    recommendation: "Upload the surge immunity test report for the power input circuit.",
  },
  {
    id: "REQ-010",
    title: "Vibration resistance shall be documented for panel mounting",
    category: "Mechanical",
    sources: 2,
    status: "Supported",
    confidence: 91,
    review: "Reviewed",
    severity: "Low",
    sourceDocument: "Environmental_Test_Report.pdf",
    evidence: [
      {
        id: "EV-12",
        label: "Environmental Test Report",
        document: "Environmental_Test_Report.pdf",
        page: 27,
        quote: "Random vibration test completed per panel mount configuration.",
        status: "Supports requirement",
      },
    ],
  },
  {
    id: "REQ-011",
    title: "Access to diagnostic port shall require authentication",
    category: "Cybersecurity",
    sources: 3,
    status: "Conflict",
    confidence: 88,
    review: "Needs review",
    severity: "Medium",
    sourceDocument: "Product_Specification_X200.pdf",
    evidence: [
      {
        id: "EV-13",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 41,
        quote: "Diagnostic port requires a service credential.",
        status: "Supports requirement",
      },
      {
        id: "EV-14",
        label: "User Manual",
        document: "User_Manual_X200.docx",
        page: 44,
        quote: "Connect to the diagnostic port to read live values; no login required.",
        status: "Potential conflict",
        highlight: "no login required",
      },
    ],
    analysis:
      "The specification and user manual describe different access control behaviour for the diagnostic port.",
    recommendation: "Confirm the shipped behaviour and align the documentation set.",
  },
  {
    id: "REQ-012",
    title: "Declared MTBF shall be supported by reliability data",
    category: "Documentation",
    sources: 1,
    status: "Partial",
    confidence: 84,
    review: "Needs review",
    severity: "Medium",
    sourceDocument: "Product_Specification_X200.pdf",
    evidence: [
      {
        id: "EV-15",
        label: "Product Specification",
        document: "Product_Specification_X200.pdf",
        page: 45,
        quote: "MTBF: 250,000 hours (calculated).",
        status: "Supporting evidence",
      },
    ],
    analysis: "The calculation method and input data set were not identified.",
    recommendation: "Attach the reliability prediction worksheet used for the MTBF figure.",
  },
];

export const findings: Finding[] = [
  {
    id: "F-001",
    type: "Potential conflict",
    requirement: "REQ-005",
    requirementTitle: "Controller input voltage tolerance shall comply with supplier specification",
    severity: "High",
    sources: 3,
    status: "Needs review",
    owner: "A. Benali",
    updated: "2 hours ago",
    category: "Electrical",
  },
  {
    id: "F-002",
    type: "Missing evidence",
    requirement: "REQ-004",
    requirementTitle: "Manufacturer shall document identified product risks",
    severity: "Critical",
    sources: 0,
    status: "Open",
    owner: "Unassigned",
    updated: "3 hours ago",
    category: "Safety",
  },
  {
    id: "F-003",
    type: "Partial evidence",
    requirement: "REQ-003",
    requirementTitle: "Device shall operate from -20°C to +70°C",
    severity: "Medium",
    sources: 2,
    status: "Needs review",
    owner: "L. Fischer",
    updated: "5 hours ago",
    category: "Environmental",
  },
  {
    id: "F-004",
    type: "Missing evidence",
    requirement: "REQ-009",
    requirementTitle: "Product shall withstand 4 kV surge on power inputs",
    severity: "High",
    sources: 0,
    status: "Open",
    owner: "Unassigned",
    updated: "Yesterday",
    category: "Electrical",
  },
  {
    id: "F-005",
    type: "Potential conflict",
    requirement: "REQ-011",
    requirementTitle: "Access to diagnostic port shall require authentication",
    severity: "Medium",
    sources: 3,
    status: "Needs review",
    owner: "S. Novak",
    updated: "Yesterday",
    category: "Cybersecurity",
  },
  {
    id: "F-006",
    type: "Partial evidence",
    requirement: "REQ-007",
    requirementTitle: "Firmware update packages shall be cryptographically signed",
    severity: "High",
    sources: 1,
    status: "Needs review",
    owner: "S. Novak",
    updated: "2 days ago",
    category: "Cybersecurity",
  },
  {
    id: "F-007",
    type: "Ambiguous requirement",
    requirement: "REQ-012",
    requirementTitle: "Declared MTBF shall be supported by reliability data",
    severity: "Medium",
    sources: 1,
    status: "Open",
    owner: "L. Fischer",
    updated: "2 days ago",
    category: "Documentation",
  },
  {
    id: "F-008",
    type: "Duplicate requirement",
    requirement: "REQ-118",
    requirementTitle: "Operating voltage range duplicated in specification annex",
    severity: "Low",
    sources: 2,
    status: "Reviewed",
    owner: "A. Benali",
    updated: "3 days ago",
    category: "Electrical",
  },
  {
    id: "F-009",
    type: "Unsupported requirement",
    requirement: "REQ-301",
    requirementTitle: "Safety documentation shall be archived for 10 years",
    severity: "High",
    sources: 0,
    status: "Open",
    owner: "Unassigned",
    updated: "3 days ago",
    category: "Documentation",
  },
  {
    id: "F-010",
    type: "Partial evidence",
    requirement: "REQ-184",
    requirementTitle: "Operating voltage requirement referenced in supplier annex",
    severity: "High",
    sources: 3,
    status: "Needs review",
    owner: "A. Benali",
    updated: "3 days ago",
    category: "Electrical",
  },
];

export const recentFindings = [
  {
    id: "REQ-184",
    requirement: "Operating voltage requirement",
    type: "Potential conflict",
    severity: "High" as Severity,
    status: "Needs review" as ReviewState,
    evidence: "3 sources",
    updated: "2h ago",
    link: "REQ-005",
  },
  {
    id: "REQ-221",
    requirement: "Over-voltage protection",
    type: "Missing evidence",
    severity: "Critical" as Severity,
    status: "Open" as ReviewState,
    evidence: "0 sources",
    updated: "3h ago",
    link: "REQ-004",
  },
  {
    id: "REQ-109",
    requirement: "Operating temperature",
    type: "Partial evidence",
    severity: "Medium" as Severity,
    status: "Needs review" as ReviewState,
    evidence: "2 sources",
    updated: "5h ago",
    link: "REQ-003",
  },
  {
    id: "REQ-301",
    requirement: "Safety documentation",
    type: "Missing evidence",
    severity: "High" as Severity,
    status: "Open" as ReviewState,
    evidence: "0 sources",
    updated: "Yesterday",
    link: "REQ-009",
  },
];

export const frameworks: Framework[] = [
  {
    id: "fw-company",
    name: "Company Requirements",
    status: "Active",
    requirements: 347,
    description: "Internal engineering requirement set maintained by Atlas Motion Systems.",
  },
  {
    id: "fw-safety",
    name: "Product Safety Checklist",
    status: "Active",
    requirements: 128,
    description: "Internal product safety review checklist used across controller programs.",
  },
  {
    id: "fw-cyber",
    name: "Cybersecurity Requirements",
    status: "Available",
    requirements: 96,
    description: "Baseline security requirements for connected industrial devices.",
  },
  {
    id: "fw-eu",
    name: "EU Product Requirements",
    status: "Configuration required",
    requirements: null,
    description: "Example requirement set used for technical documentation analysis.",
  },
];

export const notifications = [
  { id: "n1", text: "14 potential conflicts detected.", time: "2h ago", unread: true },
  { id: "n2", text: "Technical_Specification.pdf finished processing.", time: "3h ago", unread: true },
  { id: "n3", text: "REQ-004 requires human review.", time: "5h ago", unread: true },
  { id: "n4", text: "Audit report generated.", time: "Yesterday", unread: false },
];

export const reports = [
  {
    id: "RPT-001",
    name: "Technical Evidence Audit",
    project: "Industrial Controller X200",
    generated: "Generated Aug 17, 2026",
    sections: [
      "Executive summary",
      "Requirement coverage",
      "Missing evidence",
      "Potential conflicts",
      "Traceability",
      "Human review status",
    ],
  },
  {
    id: "RPT-002",
    name: "Interim Coverage Review",
    project: "Industrial Controller X200",
    generated: "Generated Aug 12, 2026",
    sections: ["Executive summary", "Requirement coverage", "Human review status"],
  },
];

export const coverageData = [
  { name: "Supported", value: projectStats.supported, key: "supported" },
  { name: "Partial", value: projectStats.partial, key: "partial" },
  { name: "Missing", value: projectStats.missing, key: "missing" },
  { name: "Conflict", value: projectStats.conflict, key: "conflict" },
];

export const categories: Category[] = [
  "Electrical",
  "Safety",
  "Environmental",
  "Mechanical",
  "Cybersecurity",
  "Documentation",
];

export const searchIndex = [
  { type: "Requirement", id: "REQ-001", label: "Operating voltage must remain within 18–32 V DC", to: "/requirements/REQ-001" },
  { type: "Requirement", id: "REQ-005", label: "Controller input voltage tolerance shall comply with supplier specification", to: "/requirements/REQ-005" },
  { type: "Requirement", id: "REQ-003", label: "Device shall operate from -20°C to +70°C", to: "/requirements/REQ-003" },
  { type: "Requirement", id: "REQ-004", label: "Manufacturer shall document identified product risks", to: "/requirements/REQ-004" },
  { type: "Document", id: "DOC-01", label: "Product_Specification_X200.pdf", to: "/documents" },
  { type: "Document", id: "DOC-04", label: "Supplier_Datasheet_MainController.pdf", to: "/documents" },
  { type: "Finding", id: "F-001", label: "Potential conflict — REQ-005 voltage tolerance", to: "/findings" },
  { type: "Project", id: "TA-2026-0042", label: "Industrial Controller X200", to: "/projects" },
];
