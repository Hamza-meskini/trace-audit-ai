import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Boxes,
  ChevronDown,
  FileStack,
  FileText,
  GitBranch,
  LayoutDashboard,
  Menu,
  ListChecks,
  PanelLeftClose,
  PanelLeft,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

const nav = [
  { to: "/app", label: "Dashboard", icon: LayoutDashboard },
  { to: "/projects", label: "Projects", icon: Boxes },
  { to: "/documents", label: "Documents", icon: FileStack },
  { to: "/requirements", label: "Requirements", icon: ListChecks },
  { to: "/findings", label: "Findings", icon: ShieldAlert },
  { to: "/traceability", label: "Traceability", icon: GitBranch },
  { to: "/reports", label: "Reports", icon: FileText },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

const labels: Record<string, string> = {
  "": "Dashboard",
  app: "Dashboard",
  projects: "Projects",
  documents: "Documents",
  requirements: "Requirements",
  findings: "Findings",
  traceability: "Traceability",
  reports: "Reports",
  frameworks: "Requirement Frameworks",
  settings: "Settings",
  "new-audit": "New Audit",
};

import { useActiveProject } from "@/hooks/use-active-project";
import { AuditProgressPanel } from "@/components/audit-progress";
import { useRequirements } from "@/hooks/use-requirements";
import { useDocuments } from "@/hooks/use-documents";
import { useFindings } from "@/hooks/use-findings";
import { useCurrentUser } from "@/hooks/use-current-user";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [openSearch, setOpenSearch] = useState(false);
  const currentUser = useCurrentUser();
  const {
    activeProject,
    activeProjectId,
    projects: apiProjects,
    selectProject,
  } = useActiveProject();
  const requirements = useRequirements(activeProjectId);
  const documents = useDocuments(activeProjectId);
  const findings = useFindings(activeProjectId);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpenSearch((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const segments = pathname.split("/").filter(Boolean);
  const crumbRoot = labels[segments[0] ?? ""] ?? "Dashboard";

  return (
    <div className="flex min-h-screen bg-surface">
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 lg:flex",
          collapsed ? "w-[68px]" : "w-60",
        )}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
          <div className="grid size-7 shrink-0 place-items-center rounded-md bg-primary text-primary-foreground">
            <GitBranch className="size-4" />
          </div>
          {!collapsed && (
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-tight">TraceAudit</div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                AI Technical Audit
              </div>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2.5">
          {nav.map((item) => {
            const active = pathname === item.to || pathname.startsWith(`${item.to}/`);
            return (
              <Link
                key={item.to}
                to={item.to}
                title={item.label}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                  active
                    ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                )}
              >
                <item.icon className={cn("size-4 shrink-0", active && "text-primary")} />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        <div className="space-y-1 border-t border-sidebar-border p-2.5">
          {(() => {
            const userName = currentUser.data?.user || "Local reviewer";
            const userSubtitle =
              currentUser.data?.email ||
              (currentUser.data?.provider === "databricks_sso"
                ? "Databricks SSO"
                : "Local workspace");
            const initials = currentUser.data?.user
              ? currentUser.data.user
                  .split(/[\s@._-]+/)
                  .filter(Boolean)
                  .slice(0, 2)
                  .map((s) => s[0]?.toUpperCase())
                  .join("")
              : "LR";
            return (
              <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
                <div className="grid size-7 shrink-0 place-items-center rounded-full bg-accent text-[11px] font-semibold">
                  {initials || "LR"}
                </div>
                {!collapsed && (
                  <div className="min-w-0 leading-tight">
                    <div className="truncate text-xs font-medium">{userName}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{userSubtitle}</div>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur md:px-6">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0 lg:hidden"
                aria-label="Open navigation"
              >
                <Menu className="size-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[min(320px,90vw)] overflow-y-auto">
              <SheetHeader>
                <SheetTitle>TraceAudit</SheetTitle>
                <SheetDescription>Technical audit workspace</SheetDescription>
              </SheetHeader>
              <nav aria-label="Mobile navigation" className="mt-6 space-y-1">
                {nav.map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={() => setMobileNavOpen(false)}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-3 text-sm",
                      (pathname === item.to || pathname.startsWith(`${item.to}/`)) &&
                        "bg-accent font-medium text-primary",
                    )}
                  >
                    <item.icon className="size-4" />
                    {item.label}
                  </Link>
                ))}
              </nav>
              <div className="mt-6 border-t pt-4">
                <p className="mb-2 text-xs font-medium text-muted-foreground">Switch project</p>
                {apiProjects.map((project) => (
                  <button
                    key={project.id}
                    onClick={() => {
                      selectProject(project.id);
                      setMobileNavOpen(false);
                    }}
                    className={cn(
                      "block w-full rounded-md p-3 text-left text-sm break-words",
                      project.id === activeProjectId && "bg-accent font-medium text-primary",
                    )}
                  >
                    {project.name}
                  </button>
                ))}
                {!apiProjects.length && (
                  <p className="text-sm text-muted-foreground">No projects yet</p>
                )}
              </div>
            </SheetContent>
          </Sheet>
          <Button
            variant="ghost"
            size="icon"
            className="hidden lg:inline-flex"
            onClick={() => setCollapsed((v) => !v)}
            aria-label="Toggle sidebar"
          >
            {collapsed ? <PanelLeft className="size-4" /> : <PanelLeftClose className="size-4" />}
          </Button>

          <div className="flex min-w-0 items-center gap-1.5 text-sm">
            <span className="hidden text-muted-foreground xl:inline">TraceAudit</span>
            <span className="hidden text-border xl:inline">/</span>
            <span className="truncate font-medium">{crumbRoot}</span>
            {segments[1] && (
              <>
                <span className="text-border">/</span>
                <span className="truncate font-mono text-xs">{segments[1]}</span>
              </>
            )}
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="hidden gap-2 sm:inline-flex">
                  <span className="size-1.5 rounded-full bg-success" />
                  <span className="max-w-[180px] truncate">
                    {activeProject?.name || "Select Project"}
                  </span>
                  <ChevronDown className="size-3.5 opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-72">
                <DropdownMenuLabel className="text-xs text-muted-foreground">
                  Projects ({apiProjects.length})
                </DropdownMenuLabel>
                {apiProjects.map((p) => (
                  <DropdownMenuItem
                    key={p.id}
                    onSelect={() => selectProject(p.id)}
                    className="flex-col items-start gap-0.5"
                  >
                    <div className="flex w-full items-center justify-between">
                      <span className="text-sm font-medium">{p.name}</span>
                      {p.id === activeProjectId && (
                        <span className="size-1.5 rounded-full bg-primary" />
                      )}
                    </div>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {p.audit_id} · {p.status}
                    </span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            <Button
              variant="ghost"
              size="sm"
              className="gap-2 text-muted-foreground"
              onClick={() => setOpenSearch(true)}
              aria-label="Search workspace"
            >
              <Search className="size-4" />
              <span className="hidden xl:inline">Search</span>
              <kbd className="hidden rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px] xl:inline">
                ⌘K
              </kbd>
            </Button>

            <div className="ml-1 grid size-7 place-items-center rounded-full bg-accent text-[11px] font-semibold">
              {(currentUser.data?.user
                ? currentUser.data.user
                    .split(/[\s@._-]+/)
                    .filter(Boolean)
                    .slice(0, 2)
                    .map((s) => s[0]?.toUpperCase())
                    .join("")
                : "LR") || "LR"}
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
          <AuditProgressPanel key={activeProjectId} projectId={activeProjectId} />
          {children}
        </main>
      </div>

      <CommandDialog open={openSearch} onOpenChange={setOpenSearch}>
        <CommandInput placeholder="Search requirements, documents, findings..." />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Requirements">
            {(requirements.data || []).map((item) => (
              <CommandItem
                key={item.id}
                value={`${item.req_code} ${item.title}`}
                onSelect={() => {
                  setOpenSearch(false);
                  navigate({
                    to: "/requirements/$id",
                    params: { id: item.id },
                    search: {
                      tab: "All",
                      query: "",
                      category: "all",
                      severity: "all",
                      document: "all",
                      issue: "all",
                      sort: "asc",
                    },
                  });
                }}
              >
                <span className="font-mono text-xs text-muted-foreground">{item.req_code}</span>
                <span className="truncate">{item.title}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandGroup heading="Documents">
            {(documents.data || []).map((item) => (
              <CommandItem
                key={item.id}
                value={item.original_filename}
                onSelect={() => {
                  setOpenSearch(false);
                  navigate({ to: "/documents" });
                }}
              >
                <span className="truncate">{item.original_filename}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandGroup heading="Findings">
            {(findings.data || []).map((item) => (
              <CommandItem
                key={item.id}
                value={`${item.finding_code} ${item.description || ""}`}
                onSelect={() => {
                  setOpenSearch(false);
                  navigate({ to: "/findings" });
                }}
              >
                <span className="font-mono text-xs text-muted-foreground">{item.finding_code}</span>
                <span className="truncate">{item.description}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  );
}

export function AiStatus({ label = "AI analysis complete" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-xs text-muted-foreground">
      <Sparkles className="size-3.5 text-primary" />
      {label}
    </span>
  );
}
