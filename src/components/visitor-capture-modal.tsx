import React, { useEffect, useState } from "react";
import {
  Building2,
  CheckCircle2,
  Mail,
  Sparkles,
  User,
  ArrowRight,
  ShieldCheck,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useRegisterVisitor } from "@/hooks/use-visitors";

const STORAGE_KEY_SUBMITTED = "traceaudit_lead_submitted";
const STORAGE_KEY_DISMISSED = "traceaudit_lead_dismissed";
const EMAIL_REGEX = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;

const ROLE_OPTIONS = [
  "Compliance & Safety",
  "Systems Engineering",
  "Automotive / Aerospace OEM",
  "Quality Assurance",
  "Executive / Management",
  "Other",
];

export function VisitorCaptureModal({
  open,
  onOpenChange,
  source = "header_button",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source?: string;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [notes, setNotes] = useState("");
  const [emailError, setEmailError] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const registerMutation = useRegisterVisitor();

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && !submitted) {
      try {
        localStorage.setItem(STORAGE_KEY_DISMISSED, String(Date.now()));
      } catch {
        // Ignore localStorage restrictions
      }
    }
    onOpenChange(nextOpen);
    if (!nextOpen) {
      setTimeout(() => {
        setSubmitted(false);
        setEmailError("");
      }, 300);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanEmail = email.trim();

    if (!cleanEmail) {
      setEmailError("Work email is required");
      return;
    }
    if (!EMAIL_REGEX.test(cleanEmail)) {
      setEmailError("Please enter a valid email address");
      return;
    }
    setEmailError("");

    try {
      await registerMutation.mutateAsync({
        email: cleanEmail,
        full_name: fullName.trim() || undefined,
        company: company.trim() || undefined,
        role: role.trim() || undefined,
        notes: notes.trim() || undefined,
        source,
      });

      try {
        localStorage.setItem(STORAGE_KEY_SUBMITTED, "true");
      } catch {
        // Ignore
      }

      setSubmitted(true);
      toast.success("Thank you! Your email has been registered.");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to register email";
      toast.error(msg);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[480px] p-0 overflow-hidden border-border bg-card shadow-2xl">
        {/* Glow Header Accent */}
        <div className="relative overflow-hidden bg-gradient-to-br from-primary/20 via-primary/5 to-transparent px-6 pt-6 pb-4 border-b border-border/50">
          <div className="flex items-center gap-2 mb-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/15 px-2.5 py-0.5 text-[11px] font-medium text-primary">
              <Sparkles className="size-3" />
              Enterprise Audit Platform
            </span>
          </div>
          <DialogTitle className="text-lg font-semibold tracking-tight text-foreground">
            {submitted ? "You're on the list!" : "Request Early Access & Updates"}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground mt-1">
            {submitted
              ? "We'll notify you with technical release notes, audit templates, and platform updates."
              : "Stay informed on AI-automated requirements compliance, multimodal evidence discovery, and verification matrices."}
          </DialogDescription>
        </div>

        <div className="p-6">
          {submitted ? (
            <div className="py-4 text-center space-y-4">
              <div className="mx-auto grid size-12 place-items-center rounded-full bg-primary/10 text-primary">
                <CheckCircle2 className="size-6 text-primary" />
              </div>
              <div className="space-y-1">
                <h3 className="text-sm font-semibold text-foreground">Registration confirmed</h3>
                <p className="text-xs text-muted-foreground max-w-sm mx-auto">
                  We have saved <span className="font-medium text-foreground">{email}</span>. Our
                  engineering team will keep you updated on new aerospace and automotive compliance
                  modules.
                </p>
              </div>
              <div className="pt-2">
                <Button
                  onClick={() => handleClose(false)}
                  className="w-full"
                  variant="outline"
                  size="sm"
                >
                  Return to Workspace
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Email */}
              <div className="space-y-1.5">
                <Label htmlFor="visitor-email" className="text-xs font-medium">
                  Work Email <span className="text-destructive">*</span>
                </Label>
                <div className="relative">
                  <Mail className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
                  <Input
                    id="visitor-email"
                    type="email"
                    placeholder="name@company.com"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      if (emailError) setEmailError("");
                    }}
                    className="pl-9 text-sm"
                    autoFocus
                    required
                  />
                </div>
                {emailError && <p className="text-[11px] text-destructive">{emailError}</p>}
              </div>

              {/* Name & Company */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="visitor-name" className="text-xs font-medium">
                    Full Name
                  </Label>
                  <div className="relative">
                    <User className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
                    <Input
                      id="visitor-name"
                      placeholder="Jane Doe"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      className="pl-9 text-sm"
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="visitor-company" className="text-xs font-medium">
                    Company
                  </Label>
                  <div className="relative">
                    <Building2 className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
                    <Input
                      id="visitor-company"
                      placeholder="OEM / Tier-1"
                      value={company}
                      onChange={(e) => setCompany(e.target.value)}
                      className="pl-9 text-sm"
                    />
                  </div>
                </div>
              </div>

              {/* Role Select Pills */}
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">Primary Focus</Label>
                <div className="flex flex-wrap gap-1.5">
                  {ROLE_OPTIONS.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => setRole(role === opt ? "" : opt)}
                      className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                        role === opt
                          ? "border-primary bg-primary text-primary-foreground font-medium"
                          : "border-border bg-muted/40 hover:bg-muted text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>

              {/* Privacy Notice */}
              <div className="flex items-center gap-2 pt-1 text-[11px] text-muted-foreground">
                <ShieldCheck className="size-3.5 text-primary shrink-0" />
                <span>No spam. Your email is stored securely in your private workspace.</span>
              </div>

              {/* Submit Button */}
              <div className="pt-2">
                <Button
                  type="submit"
                  disabled={registerMutation.isPending}
                  className="w-full text-xs font-medium gap-1.5"
                >
                  {registerMutation.isPending ? (
                    <>
                      <Loader2 className="size-3.5 animate-spin" />
                      Registering…
                    </>
                  ) : (
                    <>
                      Request Early Access & Updates
                      <ArrowRight className="size-3.5" />
                    </>
                  )}
                </Button>
              </div>
            </form>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Hook to auto-prompt first-time visitors after a delay */
export function useAutoVisitorPrompt(delayMs = 4500) {
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    try {
      const alreadySubmitted = localStorage.getItem(STORAGE_KEY_SUBMITTED);
      const alreadyDismissed = localStorage.getItem(STORAGE_KEY_DISMISSED);

      if (!alreadySubmitted) {
        let shouldShow = true;
        if (alreadyDismissed) {
          const dismissedTime = Number(alreadyDismissed);
          const threeDaysMs = 3 * 24 * 60 * 60 * 1000;
          if (Date.now() - dismissedTime < threeDaysMs) {
            shouldShow = false;
          }
        }

        if (shouldShow) {
          const timer = setTimeout(() => {
            setIsOpen(true);
          }, delayMs);
          return () => clearTimeout(timer);
        }
      }
    } catch {
      // Ignore
    }
    return undefined;
  }, [delayMs]);

  return {
    isOpen,
    setIsOpen,
  };
}
