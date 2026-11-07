import { render, screen, fireEvent } from "@testing-library/react";
import { Badge, ConfirmDialog, DataSourceBadge, StatusBadge, Table, money, pct } from "../components/ui";
import { errorMessage, ApiError } from "../api/client";

describe("DataSourceBadge", () => {
  it("labels mock data clearly and never as live", () => {
    render(<DataSourceBadge source="MOCK" />);
    expect(screen.getByText("MOCK DATA")).toBeInTheDocument();
    expect(screen.queryByText("LIVE DATA")).toBeNull();
  });
  it("labels live and mixed data", () => {
    render(<><DataSourceBadge source="LIVE" /><DataSourceBadge source="MIXED" /></>);
    expect(screen.getByText("LIVE DATA")).toBeInTheDocument();
    expect(screen.getByText("MIXED DATA")).toBeInTheDocument();
  });
  it("defaults to MOCK when the source is unknown", () => {
    render(<DataSourceBadge source={null} />);
    expect(screen.getByText("MOCK DATA")).toBeInTheDocument();
  });
});

describe("StatusBadge", () => {
  it("renders tracking states and humanises underscores", () => {
    render(<><StatusBadge value="RED" /><StatusBadge value="PENDING_APPROVAL" /></>);
    expect(screen.getByText("RED")).toBeInTheDocument();
    expect(screen.getByText("PENDING APPROVAL")).toBeInTheDocument();
  });
  it("shows MOCK CONNECTED, not Connected, for mocks", () => {
    render(<StatusBadge value="MOCK CONNECTED" />);
    expect(screen.getByText("MOCK CONNECTED")).toBeInTheDocument();
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
