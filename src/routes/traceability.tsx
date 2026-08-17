import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/primitives";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/traceability")({
  head: () => ({
    meta: [
      { title: "Traceability Map — TraceAudit" },
      {
        name: "description",
        content: "Explore requirement-to-evidence relationships across documents and findings.",
      },
      { property: "og:title", content: "Traceability Map — TraceAudit" },
      {
        property: "og:description",
        content: "Interactive map linking requirements, documents, evidence and findings.",
      },
    ],
  }),
  component: TraceabilityPage,
});

type NodeType = "Requirement" | "Document" | "Evidence" | "Finding";

const nodes: { id: string; label: string; type: NodeType; x: number; y: number }[] = [
  { id: "REQ-001", label: "REQ-001", type: "Requirement", x: 90, y: 70 },
  { id: "REQ-003", label: "REQ-003", type: "Requirement", x: 90, y: 220 },
  { id: "REQ-005", label: "REQ-005", type: "Requirement", x: 90, y: 370 },
  { id: "DOC-SPEC", label: "Product_Spec.pdf", type: "Document", x: 330, y: 70 },
  { id: "DOC-TECH", label: "Technical_Spec.pdf", type: "Document", x: 330, y: 220 },
  { id: "DOC-SUP", label: "Supplier_Datasheet.pdf", type: "Document", x: 330, y: 400 },
  { id: "EV-12", label: "Page 12", type: "Evidence", x: 580, y: 70 },
  { id: "EV-31", label: "Page 31", type: "Evidence", x: 580, y: 220 },
  { id: "EV-4", label: "Page 4", type: "Evidence", x: 580, y: 400 },
  { id: "F-001", label: "Finding F-001", type: "Finding", x: 800, y: 330 },
];

const edges: [string, string][] = [
  ["REQ-001", "DOC-SPEC"],
  ["DOC-SPEC", "EV-12"],
  ["REQ-003", "DOC-TECH"],
  ["DOC-TECH", "EV-31"],
  ["REQ-005", "DOC-SPEC"],
  ["REQ-005", "DOC-SUP"],
  ["DOC-SUP", "EV-4"],
  ["EV-4", "F-001"],
  ["EV-12", "F-001"],
];

const typeColor: Record<NodeType, string> = {
  Requirement: "var(--primary)",
  Document: "var(--muted-foreground)",
  Evidence: "var(--success)",
  Finding: "var(--critical)",
};

function TraceabilityPage() {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<string | null>("REQ-005");
  const [hidden, setHidden] = useState<NodeType[]>([]);

  const connected = new Set<string>();
  if (selected) {
    connected.add(selected);
    edges.forEach(([a, b]) => {
      if (a === selected) connected.add(b);
      if (b === selected) connected.add(a);
    });
  }

  const visible = (t: NodeType) => !hidden.includes(t);

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Traceability Map"
        subtitle="Requirement-to-evidence relationships across the indexed document set."
        actions={
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="icon" onClick={() => setZoom((z) => Math.max(0.5, z - 0.15))} aria-label="Zoom out">
              <Minus className="size-4" />
            </Button>
            <Button variant="outline" size="icon" onClick={() => setZoom((z) => Math.min(1.8, z + 0.15))} aria-label="Zoom in">
              <Plus className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setZoom(1);
                setPan({ x: 0, y: 0 });
                setSelected(null);
              }}
            >
              <RotateCcw className="size-4" />
              Reset
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Panel className="lg:col-span-3" bodyClassName="p-0">
          <div
            className="relative h-[560px] cursor-grab overflow-hidden rounded-xl bg-[radial-gradient(var(--border)_1px,transparent_1px)] [background-size:20px_20px] active:cursor-grabbing"
            onPointerDown={(e) => setDrag({ x: e.clientX - pan.x, y: e.clientY - pan.y })}
            onPointerMove={(e) => drag && setPan({ x: e.clientX - drag.x, y: e.clientY - drag.y })}
            onPointerUp={() => setDrag(null)}
            onPointerLeave={() => setDrag(null)}
          >
            <svg
              className="size-full"
              viewBox="0 0 1000 500"
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
            >
              {edges.map(([a, b]) => {
                const na = nodes.find((n) => n.id === a)!;
                const nb = nodes.find((n) => n.id === b)!;
                if (!visible(na.type) || !visible(nb.type)) return null;
                const active = selected ? connected.has(a) && connected.has(b) : true;
                return (
                  <line
                    key={`${a}-${b}`}
                    x1={na.x + 60}
                    y1={na.y + 14}
                    x2={nb.x}
                    y2={nb.y + 14}
                    stroke={active ? "var(--primary)" : "var(--border)"}
                    strokeWidth={active ? 1.6 : 1}
                    opacity={active ? 0.8 : 0.4}
                  />
                );
              })}
              {nodes.filter((n) => visible(n.type)).map((n) => {
                const active = !selected || connected.has(n.id);
                return (
                  <g
                    key={n.id}
                    transform={`translate(${n.x}, ${n.y})`}
                    onClick={() => setSelected(n.id)}
                    className="cursor-pointer"
                    opacity={active ? 1 : 0.35}
                  >
                    <rect
                      width={n.label.length > 12 ? 170 : 120}
                      height={28}
                      rx={7}
                      fill="var(--card)"
                      stroke={selected === n.id ? "var(--primary)" : "var(--border)"}
                      strokeWidth={selected === n.id ? 2 : 1}
                    />
                    <circle cx={12} cy={14} r={4} fill={typeColor[n.type]} />
                    <text x={24} y={18} fontSize={11} fill="var(--foreground)" fontFamily="IBM Plex Mono, monospace">
                      {n.label}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="Legend">
            <ul className="space-y-2 text-sm">
              {(Object.keys(typeColor) as NodeType[]).map((t) => (
                <li key={t}>
                  <button
                    onClick={() =>
                      setHidden((h) => (h.includes(t) ? h.filter((x) => x !== t) : [...h, t]))
                    }
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent",
                      hidden.includes(t) && "opacity-40",
                    )}
                  >
                    <span className="size-2.5 rounded-full" style={{ backgroundColor: typeColor[t] }} />
                    {t}
                    <span className="ml-auto text-xs text-muted-foreground">
                      {hidden.includes(t) ? "hidden" : "shown"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Selected node">
            {selected ? (
              <div className="space-y-2 text-sm">
                <div className="font-mono text-xs text-muted-foreground">{selected}</div>
                <p className="text-muted-foreground">
                  {connected.size - 1} direct relationships in the current map view.
                </p>
                <ul className="space-y-1.5 pt-1">
                  {[...connected]
                    .filter((c) => c !== selected)
                    .map((c) => (
                      <li key={c} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
                        {nodes.find((n) => n.id === c)?.label}
                      </li>
                    ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Select a node to highlight its connections.</p>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
