import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Bell,
  Boxes,
  ChevronDown,
  CircleHelp,
  FileStack,
  FileText,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  PanelLeftClose,
  PanelLeft,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  Layers,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
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
import { currentUser, notifications, projects, searchIndex } from "@/lib/mock-data";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/projects", label: "Projects", icon: Boxes },
  { to: "/documents", label: "Documents", icon: FileStack },
  { to: "/requirements", label: "Requirements", icon: ListChecks },
  { to: "/findings", label: "Findings", icon: ShieldAlert },
  { to: "/traceability", label: "Traceability", icon: GitBranch },
  { to: "/reports", label: "Reports", icon: FileText },
  { to: "/frameworks", label: "Frameworks", icon: Layers },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

const labels: Record<string, string> = {
  "": "Dashboard",
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

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [openSearch, setOpenSearch] = useState(false);
  const { activeProject, projects: apiProjects, selectProject } = useActiveProject();
  const [unread, setUnread] = useState(3);
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
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 md:flex",
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
            const active =
              item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
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
          <button className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent/60">
            <CircleHelp className="size-4 shrink-0" />
            {!collapsed && "Help"}
          </button>
          <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
            <div className="grid size-7 shrink-0 place-items-center rounded-full bg-accent text-[11px] font-semibold">
              {currentUser.initials}
            </div>
            {!collapsed && (
              <div className="min-w-0 leading-tight">
                <div className="truncate text-xs font-medium">{currentUser.name}</div>
                <div className="truncate text-[11px] text-muted-foreground">{currentUser.title}</div>
              </div>
            )}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur md:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="hidden md:inline-flex"
            onClick={() => setCollapsed((v) => !v)}
            aria-label="Toggle sidebar"
          >
            {collapsed ? <PanelLeft className="size-4" /> : <PanelLeftClose className="size-4" />}
          </Button>

          <div className="flex min-w-0 items-center gap-1.5 text-sm">
            <span className="text-muted-foreground">TraceAudit</span>
            <span className="text-border">/</span>
            <span className="truncate font-medium">{crumbRoot}</span>
            {segments[1] && (
              <>
                <span className="text-border">/</span>
                <span className="truncate font-mono text-xs">{segments[1]}</span>
              </>
            )}
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="hidden gap-2 sm:inline-flex">
                  <span className="size-1.5 rounded-full bg-success" />
                  <span className="max-w-[180px] truncate">{activeProject?.name || "Select Project"}</span>
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
                      {p.id === activeProject.id && (
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
            >
              <Search className="size-4" />
              <span className="hidden lg:inline">Search</span>
              <kbd className="hidden rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px] lg:inline">
                ⌘K
              </kbd>
            </Button>

            <DropdownMenu onOpenChange={(o) => o && setUnread(0)}>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="Notifications" className="relative">
                  <Bell className="size-4" />
                  {unread > 0 && (
                    <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-critical" />
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-80">
                <DropdownMenuLabel>Notifications</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {notifications.map((n) => (
                  <DropdownMenuItem key={n.id} className="flex-col items-start gap-0.5 py-2">
                    <span className="text-sm">{n.text}</span>
                    <span className="text-[11px] text-muted-foreground">{n.time}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            <Button variant="ghost" size="icon" aria-label="Help">
              <CircleHelp className="size-4" />
            </Button>

            <div className="ml-1 grid size-7 place-items-center rounded-full bg-accent text-[11px] font-semibold">
              {currentUser.initials}
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>

      <CommandDialog open={openSearch} onOpenChange={setOpenSearch}>
        <CommandInput placeholder="Search requirements, documents, findings..." />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          {["Requirement", "Document", "Finding", "Project"].map((group) => (
            <CommandGroup key={group} heading={group + "s"}>
              {searchIndex
                .filter((r) => r.type === group)
                .map((r) => (
                  <CommandItem
                    key={r.id}
                    value={`${r.id} ${r.label}`}
                    onSelect={() => {
                      setOpenSearch(false);
                      navigate({ to: r.to });
                    }}
                  >
                    <span className="font-mono text-xs text-muted-foreground">{r.id}</span>
                    <span className="truncate">{r.label}</span>
                  </CommandItem>
                ))}
            </CommandGroup>
          ))}
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
