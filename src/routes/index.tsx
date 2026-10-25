import { createFileRoute } from "@tanstack/react-router";
import { WelcomePage } from "@/components/welcome-page";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "TraceAudit — Trace every requirement to its evidence" },
      {
        name: "description",
        content:
          "TraceAudit connects technical requirements, source documents, test evidence, and review findings in one auditable workspace.",
      },
      { property: "og:title", content: "TraceAudit — Evidence you can trace" },
      {
        property: "og:description",
        content:
          "Explore an AI-assisted technical audit workspace built around source-linked evidence and human review.",
      },
    ],
  }),
  component: WelcomePage,
});
