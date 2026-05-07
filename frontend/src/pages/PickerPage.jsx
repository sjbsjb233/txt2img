// PickerPage — the /picker route.
//
// Switches between the deck overview (no query) and the per-session
// judging page (?session_id=…). Mounts the picker store on entry and
// keeps it warm across the route's lifetime.

import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../store/auth.js";
import * as pickerStore from "../store/picker.js";
import DeckOverview from "./picker/DeckOverview.jsx";
import PickerSession from "./picker/PickerSession.jsx";

export default function PickerPage() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get("session_id");
  const snapshot = pickerStore.usePickerSnapshot();

  useEffect(() => {
    if (user?.id) {
      void pickerStore.mountOverview(user.id);
    }
    return () => {
      pickerStore.unmount();
    };
  }, [user?.id]);

  if (!user) return null;

  if (sessionId) {
    return <PickerSession snapshot={snapshot} userId={user.id} />;
  }
  return (
    <DeckOverview
      snapshot={snapshot}
      onFinalizeAllReady={async () => {
        for (const s of snapshot.overview) {
          if (
            s.picker_state === "judging" &&
            s.final_image_id &&
            (s.stats?.unjudged ?? 0) === 0 &&
            (s.stats?.deferred ?? 0) === 0
          ) {
            try {
              await pickerStore.finalizeSession(s.id);
            } catch {
              /* swallow individual failures */
            }
          }
        }
      }}
    />
  );
}
