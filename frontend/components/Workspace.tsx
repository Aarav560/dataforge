"use client";

import "flexlayout-react/style/dark.css";
import { Actions, DockLocation, Layout, Model, type IJsonModel, type TabNode } from "flexlayout-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { DatasetsPanel } from "./DatasetsPanel";
import { CrawlFeed } from "./panels/CrawlFeed";
import { LicenseMatrix } from "./panels/LicenseMatrix";
import { PipelineCanvas } from "./panels/PipelineCanvas";
import { PlanPanel } from "./panels/PlanPanel";
import { PreviewPane } from "./panels/PreviewPane";
import { ResultWall } from "./panels/ResultWall";
import { ReviewQueue } from "./panels/ReviewQueue";
import { SourceGraph } from "./panels/SourceGraph";
import { SourcesPanel } from "./panels/SourcesPanel";
import { TeamPanel } from "./panels/TeamPanel";
import { VersionsPanel } from "./panels/VersionsPanel";

const STORAGE_KEY = "dataforge.layout.v3";

export const PANELS: Record<string, { name: string; render: () => React.ReactNode }> = {
  plan: { name: "Plan", render: () => <PlanPanel /> },
  feed: { name: "Live Crawl Feed", render: () => <CrawlFeed /> },
  graph: { name: "Source Graph", render: () => <SourceGraph /> },
  results: { name: "Result Wall", render: () => <ResultWall /> },
  canvas: { name: "Pipeline Canvas", render: () => <PipelineCanvas /> },
  preview: { name: "Preview", render: () => <PreviewPane /> },
  licenses: { name: "License Matrix", render: () => <LicenseMatrix /> },
  datasets: { name: "Datasets", render: () => <DatasetsPanel /> },
  versions: { name: "Dataset Workspace", render: () => <VersionsPanel /> },
  review: { name: "Review Queue", render: () => <ReviewQueue /> },
  team: { name: "Team & Sharing", render: () => <TeamPanel /> },
  sources: { name: "Sources", render: () => <SourcesPanel /> },
};

const tab = (id: string) => ({ type: "tab", id, component: id, name: PANELS[id].name, enableClose: false });

const DEFAULT: IJsonModel = {
  global: { tabEnableRename: false, tabSetMinWidth: 160, tabSetMinHeight: 100 },
  layout: {
    type: "row",
    children: [
      {
        type: "row",
        weight: 22,
        children: [
          { type: "tabset", weight: 60, children: [tab("plan")] },
          { type: "tabset", weight: 40, children: [tab("feed")] },
        ],
      },
      {
        type: "row",
        weight: 50,
        children: [
          { type: "tabset", weight: 45, children: [tab("graph")] },
          { type: "tabset", weight: 55, children: [tab("results"), tab("canvas"), tab("versions"), tab("review")] },
        ],
      },
      {
        type: "row",
        weight: 28,
        children: [
          { type: "tabset", weight: 55, children: [tab("preview"), tab("datasets")] },
          { type: "tabset", weight: 45, children: [tab("licenses"), tab("team"), tab("sources")] },
        ],
      },
    ],
  },
};

function loadModel(): Model {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const model = Model.fromJson(JSON.parse(saved));
      // Drop saved layouts missing a panel (e.g. after an upgrade).
      if (Object.keys(PANELS).every((id) => model.getNodeById(id))) return model;
    }
  } catch {
    // storage blocked or corrupt layout
  }
  return Model.fromJson(DEFAULT);
}

export function useWorkspaceLayout() {
  const [model, setModel] = useState<Model | null>(null);
  const [narrow, setNarrow] = useState(false);
  const [mobileTab, setMobileTab] = useState("results");

  useEffect(() => {
    setModel(loadModel());
    const mq = window.matchMedia("(max-width: 900px)");
    const update = () => setNarrow(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  const reset = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    setModel(Model.fromJson(DEFAULT));
  }, []);

  const show = useCallback(
    (id: string) => {
      setMobileTab(id);
      if (model?.getNodeById(id)) model.doAction(Actions.selectTab(id));
      else {
        // Panels can't be closed, but a stale saved layout might lack one.
        const target = model?.getActiveTabset() ?? model?.getFirstTabSet();
        if (target) model?.doAction(Actions.addNode(tab(id), target.getId(), DockLocation.CENTER, -1));
      }
    },
    [model],
  );

  return { model, narrow, mobileTab, setMobileTab, reset, show };
}

export function WorkspaceLayout({ model, narrow, mobileTab, setMobileTab }: ReturnType<typeof useWorkspaceLayout>) {
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  if (!model) return null;

  if (narrow) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b border-[var(--border)] px-2 py-1">
          {Object.entries(PANELS).map(([id, p]) => (
            <button key={id} className={`btn shrink-0 ${mobileTab === id ? "border-[var(--accent)] text-[var(--accent)]" : ""}`} onClick={() => setMobileTab(id)}>
              {p.name}
            </button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-auto">{PANELS[mobileTab].render()}</div>
      </div>
    );
  }

  return (
    <div className="relative min-h-0 flex-1">
      <Layout
        model={model}
        factory={(node: TabNode) => PANELS[node.getComponent() ?? ""]?.render() ?? null}
        onModelChange={(m) => {
          clearTimeout(saveTimer.current);
          saveTimer.current = setTimeout(() => {
            try {
              localStorage.setItem(STORAGE_KEY, JSON.stringify(m.toJson()));
            } catch {
              // storage blocked
            }
          }, 300);
        }}
      />
    </div>
  );
}
