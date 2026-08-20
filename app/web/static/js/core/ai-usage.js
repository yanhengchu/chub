"use strict";

(() => {
  const CACHE_KEY = "hub.aiUsageCache";
  const REFRESH_MS = 5 * 60 * 1000;
  const listeners = new Set();
  let snapshot = null;
  let loadedAt = 0;
  let loadPromise = null;
  let loadForced = false;
  let queuedForcePromise = null;
  let version = 0;

  function removeStoredSnapshot() {
    try {
      sessionStorage.removeItem(CACHE_KEY);
    } catch (_error) {
      // Storage failures must not affect live usage requests.
    }
  }

  function restoreSnapshot() {
    try {
      const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
      if (!cached || typeof cached !== "object") {
        removeStoredSnapshot();
        return;
      }
      snapshot = cached;
      const checkedAt = new Date(cached.checked_at).getTime();
      loadedAt = Number.isNaN(checkedAt) || checkedAt > Date.now()
        ? 0
        : checkedAt;
    } catch (_error) {
      snapshot = null;
      loadedAt = 0;
      removeStoredSnapshot();
    }
  }

  function notify(nextSnapshot) {
    listeners.forEach((listener) => {
      try {
        listener(nextSnapshot);
      } catch (_error) {
        // One consumer must not block other usage consumers.
      }
    });
  }

  function storeSnapshot(data) {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify(data));
    } catch (_error) {
      // Storage failures must not affect the live snapshot.
    }
  }

  function requestError(response, payload) {
    const error = new Error(
      payload?.error?.message || `AI usage request failed (HTTP ${response.status})`,
    );
    error.code = response.status === 401
      ? "invalid_credentials"
      : payload?.error?.code || "ai_usage_unavailable";
    error.status = response.status;
    return error;
  }

  function releaseLoad(currentPromise) {
    if (loadPromise === currentPromise) {
      loadPromise = null;
      loadForced = false;
    }
  }

  function startLoad(force) {
    const requestVersion = version;
    const currentPromise = (async () => {
      try {
        const response = await fetch(
          `/api/ai/usage${force ? "?refresh=true" : ""}`,
          {
            cache: "no-store",
          },
        );
        let payload;
        try {
          payload = await response.json();
        } catch (_error) {
          throw requestError(response, null);
        }
        if (!response.ok || payload.success !== true) {
          throw requestError(response, payload);
        }
        if (requestVersion !== version) {
          return null;
        }
        snapshot = payload.data;
        loadedAt = Date.now();
        storeSnapshot(snapshot);
        notify(snapshot);
        return snapshot;
      } catch (error) {
        if (requestVersion === version) {
          snapshot = null;
          loadedAt = 0;
          if (error?.code === "invalid_credentials") {
            removeStoredSnapshot();
          }
          notify(null);
        }
        throw error;
      }
    })();
    loadPromise = currentPromise;
    loadForced = force;
    void currentPromise.then(
      () => releaseLoad(currentPromise),
      () => releaseLoad(currentPromise),
    );
    return currentPromise;
  }

  function queueForcedLoad() {
    if (queuedForcePromise) {
      return queuedForcePromise;
    }
    const pending = loadPromise;
    const requestVersion = version;
    const followUp = pending
      .catch(() => null)
      .then(() => {
        if (requestVersion !== version) {
          return null;
        }
        releaseLoad(pending);
        return startLoad(true);
      });
    queuedForcePromise = followUp;
    void followUp.then(
      () => {
        if (queuedForcePromise === followUp) {
          queuedForcePromise = null;
        }
      },
      () => {
        if (queuedForcePromise === followUp) {
          queuedForcePromise = null;
        }
      },
    );
    return followUp;
  }

  function load({ force = false } = {}) {
    if (!force && snapshot && Date.now() - loadedAt < REFRESH_MS) {
      return Promise.resolve(snapshot);
    }
    if (loadPromise) {
      if (force && !loadForced) {
        return queueForcedLoad();
      }
      return loadPromise;
    }
    return startLoad(force);
  }

  function clear() {
    version += 1;
    loadPromise = null;
    loadForced = false;
    queuedForcePromise = null;
    snapshot = null;
    loadedAt = 0;
    removeStoredSnapshot();
    notify(null);
  }

  function subscribe(listener) {
    if (typeof listener !== "function") {
      return () => {};
    }
    listeners.add(listener);
    listener(snapshot);
    return () => listeners.delete(listener);
  }

  restoreSnapshot();
  window.ChubAiUsage = {
    clear,
    current: () => snapshot,
    load,
    subscribe,
  };
})();
