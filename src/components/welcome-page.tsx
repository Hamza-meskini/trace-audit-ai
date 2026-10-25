import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowRight, ArrowUpRight, Check, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import "./welcome.css";

const workflow = [
  {
    label: "Read the requirement",
    title: "Start with what must be true.",
    description:
      "A requirement is more than a sentence. TraceAudit helps identify the conditions, limits, and source context your evidence needs to address.",
    detail:
      "In this example, the bus must discharge below 60 volts within 5 seconds of the shutdown trigger.",
    source: "System Requirements Specification · Page 1",
  },
  {
    label: "Find the evidence",
    title: "Bring the source into view.",
    description:
      "Connect the requirement to the relevant test evidence. Inspect the cited measurements and their context, with a clear path back to the source document.",
    detail:
      "Table 2.1 records 41.8 V at 3.42 seconds for the first discharge run. Both values are inside the stated limits.",
    source: "Electrical & Safety Test Report · Page 2, Table 2.1",
  },
  {
    label: "Make the review decision",
    title: "Keep the reasoning with the result.",
    description:
      "Review supported requirements, investigate gaps and conflicts, and record the decisions your team makes. The evidence remains part of the story.",
    detail:
      "The expected result for this benchmark requirement is Supported. A reviewer still checks the evidence and confirms the conclusion.",
    source: "REQ-BMS-001 · Benchmark expected result",
  },
] as const;

function Brand() {
  return (
    <span className="ta-brand">
      <img src="/logo.svg" width="42" height="27" alt="" />
      <span>TraceAudit</span>
    </span>
  );
}

function EvidenceStory() {
  const [step, setStep] = useState(0);
  const current = workflow[step]!;
  return (
    <section id="how-it-works" className="ta-story ta-wrap" aria-labelledby="story-title">
      <div className="ta-section-heading">
        <h2 id="story-title">
          Follow the evidence.
          <br />
          <span>Understand the answer.</span>
        </h2>
        <p>
          Specifications, reports, and review decisions belong in the same conversation. See how one
          requirement connects them.
        </p>
      </div>
      <div className="ta-story-steps" aria-label="Explore the example workflow">
        {workflow.map((item, index) => (
          <button
            key={item.label}
            type="button"
            aria-pressed={step === index}
            onClick={() => setStep(index)}
          >
            <span className="ta-step-number">0{index + 1}</span>
            <span>{item.label}</span>
            <ArrowUpRight size={19} />
          </button>
        ))}
      </div>
      <div className="ta-story-stage">
        <div className="ta-source-stage">
          <div className="ta-source-heading">
            <span>From the sample audit package</span>
            <span>REQ-BMS-001</span>
          </div>
          <div className="ta-source-content" key={step}>
            {step === 0 ? (
              <div className="ta-requirement-sheet">
                <div className="ta-sheet-rule" />
                <span className="ta-sheet-label">System requirements specification</span>
                <h3>
                  High-voltage bus
                  <br />
                  active discharge timing
                </h3>
                <p>
                  The DC bus voltage shall drop from its nominal operating potential to{" "}
                  <mark>below 60.0 V</mark> within a maximum allowable duration of{" "}
                  <mark>5.00 seconds.</mark>
                </p>
                <div className="ta-requirement-limits">
                  <div>
                    <strong>
                      &lt; 60<span> V</span>
                    </strong>
                    <small>Voltage threshold</small>
                  </div>
                  <div>
                    <strong>
                      ≤ 5<span> s</span>
                    </strong>
                    <small>Maximum duration</small>
                  </div>
                </div>
                <span className="ta-sheet-foot">Requirement excerpt · SRS, page 1</span>
              </div>
            ) : step === 1 ? (
              <div className="ta-evidence-sheet">
                <img
                  src="/images/discharge-evidence.png"
                  width="740"
                  height="485"
                  alt="Sample discharge test graph and measurement table. Table 2.1 records 41.8 volts at 3.42 seconds for run 1."
                  loading="lazy"
                />
                <span className="ta-sheet-foot">Source extract · Test report, page 2</span>
              </div>
            ) : (
              <div className="ta-review-sheet">
                <span className="ta-result">
                  <Check size={18} /> Supported in this sample
                </span>
                <h3>
                  A conclusion you
                  <br />
                  can inspect.
                </h3>
                <div className="ta-review-row">
                  <span>Voltage</span>
                  <span>
                    <strong>41.8 V</strong> / below 60.0 V
                  </span>
                  <Check size={16} />
                </div>
                <div className="ta-review-row">
                  <span>Time</span>
                  <span>
                    <strong>3.42 s</strong> / within 5.00 s
                  </span>
                  <Check size={16} />
                </div>
                <p>
                  Evidence: Table 2.1, discharge run 1.
                  <br />
                  Final review stays with your team.
                </p>
                <span className="ta-sheet-foot">
                  Expected benchmark result · Human confirmation required
                </span>
              </div>
            )}
          </div>
        </div>
        <div className="ta-story-copy" aria-live="polite" aria-atomic="true">
          <span className="ta-story-counter">
            0{step + 1} <span>/ 03</span>
          </span>
          <div key={step} className="ta-story-copy-inner">
            <h3>{current.title}</h3>
            <p>{current.description}</p>
            <div className="ta-story-detail">{current.detail}</div>
            <small>{current.source}</small>
          </div>
          <button
            type="button"
            className="ta-story-next"
            onClick={() => setStep((step + 1) % workflow.length)}
          >
            {step === 2 ? "Explore again" : "Continue the trace"}
            <ArrowRight size={20} />
          </button>
        </div>
      </div>
      <p className="ta-example-note">
        An educational example from TraceAudit’s sample BMS audit package. These are sample
        documents and expected results, not a live audit.
      </p>
    </section>
  );
}

export function WelcomePage() {
  const heroRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const hero = heroRef.current;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!hero) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const rect = hero.getBoundingClientRect();
      const offset = motion.matches ? 0 : Math.min(60, Math.max(0, -rect.top * 0.12));
      hero.style.setProperty("--photo-offset", `${offset}px`);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", schedule, { passive: true });
    motion.addEventListener("change", schedule);
    update();
    return () => {
      window.removeEventListener("scroll", schedule);
      motion.removeEventListener("change", schedule);
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="ta-page">
      <a className="ta-skip" href="#main-content">
        Skip to content
      </a>
      <header className="ta-header ta-wrap">
        <Link to="/" aria-label="TraceAudit home">
          <Brand />
        </Link>
        <nav aria-label="Main navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#workspace">The workspace</a>
          <a href="#about">Our approach</a>
        </nav>
        <Link className="ta-button ta-button-dark ta-header-action" to="/app">
          Test the app <ArrowUpRight size={17} />
        </Link>
      </header>
      <main id="main-content">
        <section className="ta-hero" ref={heroRef} aria-labelledby="hero-title">
          <div className="ta-hero-top ta-wrap">
            <div>
              <p className="ta-overline">AI-assisted technical documentation audits</p>
              <h1 id="hero-title">
                <span>Every requirement</span>
                <span>
                  needs proof<span className="ta-period">.</span>
                </span>
              </h1>
            </div>
            <div className="ta-hero-intro">
              <p>
                Connect requirements to the evidence behind them. Find the gaps. Give every review
                decision a clear foundation.
              </p>
              <Link to="/app" className="ta-text-link">
                Explore TraceAudit <ArrowUpRight size={20} />
              </Link>
            </div>
          </div>
          <div className="ta-hero-image">
            <img
              src="/images/circuit-detail.webp"
              alt="Close-up photograph of a circuit board showing the dense network of components and electrical traces."
              width="1800"
              height="1200"
              fetchPriority="high"
            />
            <div className="ta-image-shade" />
            <div className="ta-image-annotation" aria-hidden="true">
              <span className="ta-focus-corner" />
              <span className="ta-annotation-line" />
              <span>Complexity, connected.</span>
            </div>
            <div className="ta-image-bottom">
              <span>
                Real systems.
                <br />
                Traceable decisions.
              </span>
              <a href="#how-it-works">
                Follow the evidence <ArrowDown size={20} />
              </a>
            </div>
          </div>
          <div className="ta-hero-caption ta-wrap">
            <p>Built for the people behind reliable systems.</p>
            <span>
              Engineering <i>/</i> Quality <i>/</i> Technical compliance
            </span>
          </div>
        </section>

        <section id="about" className="ta-intro ta-wrap" aria-labelledby="intro-title">
          <span className="ta-section-index">The purpose</span>
          <h2 id="intro-title">
            The work is complex.
            <br />
            The evidence should be clear.
          </h2>
          <div>
            <p>
              Technical audits span specifications, test results, and countless details. TraceAudit
              brings those connections into view.
            </p>
            <p>
              It helps your team understand what is supported, what is missing, and what deserves a
              closer look—with the source always within reach.
            </p>
          </div>
        </section>
        <EvidenceStory />

        <section id="workspace" className="ta-workspace" aria-labelledby="workspace-title">
          <div className="ta-workspace-photo">
            <img
              src="/images/engineering-workbench.webp"
              width="2200"
              height="1236"
              alt="An electronics workbench with a circuit design display, oscilloscope, multimeter, and a connected circuit board."
              loading="lazy"
            />
            <span>Where evidence begins.</span>
          </div>
          <div className="ta-workspace-copy">
            <span className="ta-section-index">The audit workspace</span>
            <h2 id="workspace-title">
              Keep the detail.
              <br />
              See the bigger picture.
            </h2>
            <p>
              Move between your documents, requirements, findings, and traceability register in one
              connected workspace.
            </p>
            <div className="ta-workspace-links">
              <Link to="/documents">
                <span>01</span>
                <div>
                  <strong>Documents & requirements</strong>
                  <small>Bring your source material together.</small>
                </div>
                <ArrowUpRight size={20} />
              </Link>
              <Link to="/findings">
                <span>02</span>
                <div>
                  <strong>Findings & review</strong>
                  <small>Investigate gaps and record decisions.</small>
                </div>
                <ArrowUpRight size={20} />
              </Link>
              <Link to="/traceability">
                <span>03</span>
                <div>
                  <strong>Traceability & reporting</strong>
                  <small>Follow the evidence behind each result.</small>
                </div>
                <ArrowUpRight size={20} />
              </Link>
            </div>
            <Link to="/app" className="ta-button ta-button-dark">
              Open the workspace <ArrowUpRight size={18} />
            </Link>
          </div>
        </section>

        <section className="ta-human ta-wrap" aria-labelledby="human-title">
          <Plus size={30} strokeWidth={1} />
          <h2 id="human-title">
            AI helps connect the evidence.
            <br />
            <span>Your team makes the call.</span>
          </h2>
          <p>
            Review the source, challenge the finding, and confirm the conclusion. TraceAudit
            supports engineering judgment at every step.
          </p>
        </section>
        <section className="ta-final" aria-labelledby="final-title">
          <div className="ta-wrap">
            <p>Your next audit starts here.</p>
            <div>
              <h2 id="final-title">Make it traceable.</h2>
              <Link to="/app" className="ta-final-action">
                Test the app <ArrowUpRight size={28} />
              </Link>
            </div>
          </div>
        </section>
      </main>
      <footer className="ta-footer ta-wrap">
        <Brand />
        <span>Technical evidence. Clear decisions.</span>
        <div>
          <a
            href="https://unsplash.com/photos/macro-photography-of-black-circuit-board-FO7JIlwjOtU"
            target="_blank"
            rel="noreferrer"
          >
            Photography: Alexandre Debiève
          </a>
          <a
            href="https://unsplash.com/photos/a-couple-of-electronic-devices-sitting-on-top-of-a-table-Ky0B3cBJGKo"
            target="_blank"
            rel="noreferrer"
          >
            & Axel Richter / Unsplash
          </a>
        </div>
      </footer>
    </div>
  );
}
