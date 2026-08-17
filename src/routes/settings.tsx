import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { PageHeader, Panel } from "@/components/primitives";
import { Tag } from "@/components/status";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({
    meta: [
      { title: "Settings — TraceAudit" },
      { name: "description", content: "Organization, roles, retention and audit log settings." },
      { property: "og:title", content: "Settings — TraceAudit" },
      { property: "og:description", content: "Enterprise configuration for your audit workspace." },
    ],
  }),
  component: SettingsPage,
});

const sections = [
  "Organization",
  "Users & roles",
  "Projects",
  "AI configuration",
  "Document retention",
  "Audit logs",
  "Security",
] as const;

const users = [
  { name: "Hamza Meskini", role: "Admin", email: "h.meskini@atlasmotion.com" },
  { name: "A. Benali", role: "Reviewer", email: "a.benali@atlasmotion.com" },
  { name: "L. Fischer", role: "Engineer", email: "l.fischer@atlasmotion.com" },
  { name: "S. Novak", role: "Viewer", email: "s.novak@atlasmotion.com" },
];

function SettingsPage() {
  const [active, setActive] = useState<(typeof sections)[number]>("Organization");

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader title="Settings" subtitle="Workspace configuration for Atlas Motion Systems." />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <nav className="space-y-1 lg:col-span-1">
          {sections.map((s) => (
            <button
              key={s}
              onClick={() => setActive(s)}
              className={cn(
                "w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                active === s
                  ? "bg-card font-medium shadow-subtle"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              {s}
            </button>
          ))}
        </nav>

        <div className="space-y-4 lg:col-span-3">
          {active === "Users & roles" ? (
            <Panel title="Users & roles" bodyClassName="p-0">
              <table className="w-full text-sm">
                <tbody>
                  {users.map((u) => (
                    <tr key={u.email} className="border-b border-border/70 last:border-0">
                      <td className="px-5 py-3 font-medium">{u.name}</td>
                      <td className="px-5 py-3 text-muted-foreground">{u.email}</td>
                      <td className="px-5 py-3">
                        <Tag>{u.role}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          ) : (
            <Panel title={active}>
              <div className="space-y-4">
                <div>
                  <label className="text-xs uppercase tracking-wide text-muted-foreground">
                    Organization name
                  </label>
                  <Input className="mt-1.5" defaultValue="Atlas Motion Systems" />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div>
                    <div className="text-sm font-medium">Require human review on critical findings</div>
                    <p className="text-xs text-muted-foreground">
                      Critical findings cannot be closed without a reviewer decision.
                    </p>
                  </div>
                  <Switch defaultChecked />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div>
                    <div className="text-sm font-medium">Retain source documents</div>
                    <p className="text-xs text-muted-foreground">Retention period: 24 months.</p>
                  </div>
                  <Switch defaultChecked />
                </div>
                <Button size="sm">Save changes</Button>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
