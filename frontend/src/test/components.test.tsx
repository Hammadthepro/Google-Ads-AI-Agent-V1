import { render, screen, fireEvent } from "@testing-library/react";
import { Badge, ConfirmDialog, DataSourceBadge, DataStatusNotice, ErrorBox, StatusBadge, Table, money, pct } from "../components/ui";
import { errorMessage, ApiError } from "../api/client";

describe("DataSourceBadge", () => {
  it("labels test fixtures clearly and never as live", () => {
    render(<DataSourceBadge source="MOCK" />);
    expect(screen.getByText("TEST FIXTURE")).toBeInTheDocument();
    expect(screen.queryByText("LIVE DATA")).toBeNull();
  });
  it("labels live and mixed data", () => {
    render(<><DataSourceBadge source="LIVE" /><DataSourceBadge source="MIXED" /></>);
    expect(screen.getByText("LIVE DATA")).toBeInTheDocument();
    expect(screen.getByText("MIXED DATA")).toBeInTheDocument();
  });
  it("never claims live data when the source is unknown", () => {
    render(<DataSourceBadge source={null} />);
    expect(screen.getByText("NO DATA SOURCE")).toBeInTheDocument();
    expect(screen.queryByText("LIVE DATA")).toBeNull();
  });
});

describe("StatusBadge", () => {
  it("renders tracking states and humanises underscores", () => {
    render(<><StatusBadge value="FAILED" /><StatusBadge value="PENDING_APPROVAL" /></>);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText("PENDING APPROVAL")).toBeInTheDocument();
  });
});

describe("DataStatusNotice", () => {
  it("tells the user to configure Google Ads instead of showing numbers", () => {
    render(<DataStatusNotice status={{ state: "not_configured" }} />);
    expect(screen.getByText(/Google Ads integration is not configured/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Integrations page" })).toHaveAttribute("href", "/integrations");
  });
  it("labels data from a failed refresh as historical with its original retrieval time", () => {
    render(<DataStatusNotice status={{ state: "historical", retrieved_at: "2026-11-01T10:00:00Z", message: "The latest refresh failed." }} />);
    expect(screen.getByText("Historical data")).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed/)).toBeInTheDocument();
  });
  it("says there is no data when connected but nothing was retrieved", () => {
    render(<DataStatusNotice status={{ state: "no_data" }} />);
    expect(screen.getByText(/No Google Ads data available/)).toBeInTheDocument();
  });
  it("shows nothing when the data is current", () => {
    const { container } = render(<DataStatusNotice status={{ state: "data_available", retrieved_at: "2026-11-01T10:00:00Z" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ErrorBox", () => {
  it("links to the Integrations page for a not-configured integration", () => {
    const err = Object.assign(new Error("Groq (internal AI) integration is not configured. Please configure and test your connection on the Integrations page."), { code: "integration_not_configured" });
    render(<ErrorBox error={err} />);
    expect(screen.getByRole("link", { name: "Open the Integrations page" })).toHaveAttribute("href", "/integrations");
  });
});

describe("Table", () => {
  it("renders an empty state", () => {
    render(<Table rows={[]} columns={[{ key: "a", label: "A" }]} empty="Nothing" />);
    expect(screen.getByText("Nothing")).toBeInTheDocument();
  });
  it("renders rows and handles clicks", () => {
    const onClick = vi.fn();
    render(<Table rows={[{ id: "1", a: "hello" }]} columns={[{ key: "a", label: "A" }]} onRowClick={onClick} />);
    fireEvent.click(screen.getByText("hello"));
    expect(onClick).toHaveBeenCalledWith({ id: "1", a: "hello" });
  });
});

describe("ConfirmDialog", () => {
  it("only confirms when the user clicks confirm", () => {
    const yes = vi.fn(), no = vi.fn();
    render(<ConfirmDialog open title="Approve?" confirmLabel="Approve" onConfirm={yes} onCancel={no} />);
    fireEvent.click(screen.getByText("Cancel"));
    expect(no).toHaveBeenCalled();
    expect(yes).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Approve"));
    expect(yes).toHaveBeenCalled();
  });
  it("renders nothing when closed", () => {
    render(<ConfirmDialog open={false} title="Hidden" onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.queryByText("Hidden")).toBeNull();
  });
});

describe("formatting & errors", () => {
  it("formats money and percentages", () => {
    expect(money(1234.5)).toBe("$1,234.50");
    expect(pct(0.123)).toBe("12.3%");
    expect(money(null)).toBe("—");
  });
  it("includes safety-block reasons in error messages", () => {
    const e = new ApiError(423, "safety_blocked", "Write blocked", { reasons: ["Kill switch on: GLOBAL"] });
    expect(errorMessage(e)).toContain("Kill switch on: GLOBAL");
  });
  it("Badge renders children", () => {
    render(<Badge>hi</Badge>);
    expect(screen.getByText("hi")).toBeInTheDocument();
  });
});

import { EvidenceBadge } from "../pages/AuditReportPage";
describe("EvidenceBadge", () => {
  it("labels the five evidence types", () => {
    render(<>{["VERIFIED", "ESTIMATED", "AI_GENERATED", "UNAVAILABLE", "NOT_APPLICABLE"].map((v) => <EvidenceBadge key={v} v={v} />)}</>);
    for (const t of ["Verified", "Estimated", "AI-generated", "Unavailable", "Not applicable"]) expect(screen.getByText(t)).toBeInTheDocument();
  });
});
