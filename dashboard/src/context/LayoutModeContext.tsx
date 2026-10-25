import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  loadLayoutMode,
  loadMinimalNavPane,
  saveLayoutMode,
  saveMinimalNavPane,
  type LayoutMode,
  type MinimalNavPane,
} from "../layouts/layoutModeStorage";
import { isPlatEmbedded } from "../utils/platEmbed";

interface LayoutModeContextValue {
  isPlatEmbedded: boolean;
  layoutMode: LayoutMode;
  setLayoutMode: (mode: LayoutMode) => void;
  /** Lower pane in minimal layout (settings vs records). Ignored in classic. */
  minimalPane: MinimalNavPane;
  setMinimalPane: (pane: MinimalNavPane) => void;
}

const LayoutModeContext = createContext<LayoutModeContextValue>({
  isPlatEmbedded: false,
  layoutMode: "minimal",
  setLayoutMode: () => {},
  minimalPane: "records",
  setMinimalPane: () => {},
});

export function LayoutModeProvider({ children }: { children: ReactNode }) {
  const embedded = isPlatEmbedded();
  const [layoutMode, setLayoutModeState] = useState<LayoutMode>(() =>
    loadLayoutMode(),
  );
  const [minimalPane, setMinimalPaneState] = useState<MinimalNavPane>(() =>
    loadMinimalNavPane(),
  );

  const setLayoutMode = useCallback(
    (mode: LayoutMode) => {
      if (embedded) return;
      setLayoutModeState(mode);
      saveLayoutMode(mode);
    },
    [embedded],
  );

  const setMinimalPane = useCallback(
    (pane: MinimalNavPane) => {
      if (embedded) return;
      setMinimalPaneState(pane);
      saveMinimalNavPane(pane);
    },
    [embedded],
  );

  const value = useMemo(
    () => ({
      isPlatEmbedded: embedded,
      layoutMode: embedded ? ("minimal" as const) : layoutMode,
      setLayoutMode,
      minimalPane: embedded ? ("records" as const) : minimalPane,
      setMinimalPane,
    }),
    [embedded, layoutMode, setLayoutMode, minimalPane, setMinimalPane],
  );

  return (
    <LayoutModeContext.Provider value={value}>
      {children}
    </LayoutModeContext.Provider>
  );
}

export function useLayoutMode(): LayoutModeContextValue {
  return useContext(LayoutModeContext);
}
