import { ComponentType } from "react";
import { AppFull } from "../hooks/useAppsApi";

export interface DesktopAppProps {
  app: AppFull;
}

export type DesktopAppComponent = ComponentType<DesktopAppProps>;

type DesktopAppModule = {
  default?: ComponentType<unknown>;
};

function normalizeKey(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function buildDesktopAppRegistry(): Record<string, DesktopAppComponent> {
  const modules = import.meta.glob<DesktopAppModule>(
    "./*/index.{ts,tsx}",
    { eager: true }
  );

  return Object.fromEntries(
    Object.entries(modules).flatMap(([modulePath, module]) => {
      if (!module.default) {
        return [];
      }

      const match = modulePath.match(/\.\/([^/]+)\/index\.(?:ts|tsx)$/);
      if (!match) {
        return [];
      }

      return [[normalizeKey(match[1]), module.default as DesktopAppComponent]];
    })
  );
}

// Product apps must be mounted inside AppViewer windows. Generated apps should
// expose a default component from frontend/src/apps/<AppName>/index.ts[x].
const desktopAppRegistry = buildDesktopAppRegistry();

export function getDesktopAppComponent(app: AppFull): DesktopAppComponent | null {
  const metadataEntry =
    app.metadata_json &&
    typeof app.metadata_json.frontend_entry === "string"
      ? normalizeKey(app.metadata_json.frontend_entry)
      : null;

  const candidates = [
    metadataEntry,
    normalizeKey(app.name),
  ].filter((candidate): candidate is string => Boolean(candidate));

  for (const candidate of candidates) {
    if (candidate in desktopAppRegistry) {
      return desktopAppRegistry[candidate];
    }
  }

  return null;
}
