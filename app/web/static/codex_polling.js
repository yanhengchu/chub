"use strict";

(function exposeCodexPollPlan(root) {
  function codexPollPlan({
    sessions,
    stateChanged,
    unchangedSince,
    now,
    visible,
    authenticated,
    mutating,
    fastDelay,
    slowDelay,
    slowAfter,
  }) {
    const hasWorkingSession = sessions.some(
      (session) => session.status === "running" && session.activity === "working",
    );
    const hasUnknownSession = sessions.some(
      (session) => session.status === "running" && session.activity === "unknown",
    );
    const shouldPoll = hasWorkingSession || hasUnknownSession;
    if (!shouldPoll) {
      return { shouldPoll: false, unchangedSince: 0, delay: null };
    }

    const nextUnchangedSince = stateChanged || !unchangedSince
      ? now
      : unchangedSince;
    if (!visible || !authenticated || mutating) {
      return {
        shouldPoll: true,
        unchangedSince: nextUnchangedSince,
        delay: null,
      };
    }
    return {
      shouldPoll: true,
      unchangedSince: nextUnchangedSince,
      delay: !hasWorkingSession || now - nextUnchangedSince >= slowAfter
        ? slowDelay
        : fastDelay,
    };
  }

  root.codexPollPlan = codexPollPlan;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { codexPollPlan };
  }
})(typeof globalThis === "undefined" ? this : globalThis);
