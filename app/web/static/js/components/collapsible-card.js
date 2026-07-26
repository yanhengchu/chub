"use strict";

const CARD_COLLAPSED_STATE_KEY = "hub.cardCollapsedState.v1";
const CARD_FADE_DURATION_MS = 140;
const CARD_HEIGHT_DURATION_MS = 180;
let cardCollapsedState = {};

function loadCardCollapsedState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CARD_COLLAPSED_STATE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => typeof value === "boolean"),
    );
  } catch (_error) {
    return {};
  }
}

function saveCardCollapsedState() {
  try {
    localStorage.setItem(
      CARD_COLLAPSED_STATE_KEY,
      JSON.stringify(cardCollapsedState),
    );
  } catch (_error) {
    // Storage can be unavailable in private browsing; keep the in-memory state.
  }
}

function setupCollapsibleCard(card) {
  if (!card || card.dataset.collapsibleReady === "1") {
    return;
  }
  const heading = card.querySelector(".section-heading");
  const headingCopy = card.querySelector("[data-card-heading]");
  const content = card.querySelector("[data-card-content]");
  if (!heading || !headingCopy || !content) {
    return;
  }
  card.dataset.collapsibleCard = "";
  const cardKey = card.dataset.cardKey;
  if (!cardKey) {
    return;
  }
  const initiallyCollapsed = typeof cardCollapsedState[cardKey] === "boolean"
    ? cardCollapsedState[cardKey]
    : card.dataset.collapsed === "true";
  headingCopy.setAttribute("role", "button");
  headingCopy.tabIndex = 0;
  headingCopy.setAttribute("aria-expanded", String(!initiallyCollapsed));
  card.classList.toggle("is-collapsed", initiallyCollapsed);
  content.hidden = initiallyCollapsed;
  content.inert = initiallyCollapsed;
  card.getBoundingClientRect();
  card.dataset.collapsibleReady = "1";
  let collapseAnimationVersion = 0;
  let contentAnimations = [];

  const cancelContentAnimations = () => {
    contentAnimations.forEach((animation) => animation.cancel());
    contentAnimations = [];
  };

  const playContentAnimation = (target, keyframes, options) => {
    const animation = target.animate(keyframes, options);
    contentAnimations.push(animation);
    return animation.finished.catch(() => {});
  };

  const setContentCollapsed = async (collapsed) => {
    collapseAnimationVersion += 1;
    const animationVersion = collapseAnimationVersion;
    const wasHidden = content.hidden;
    const startHeight = wasHidden ? 0 : content.getBoundingClientRect().height;
    const fadeTargets = Array.from(content.children);
    if (fadeTargets.length === 0) {
      fadeTargets.push(content);
    }
    const startOpacities = fadeTargets.map((target) => (
      wasHidden ? 0 : Number.parseFloat(window.getComputedStyle(target).opacity)
    ));
    cancelContentAnimations();
    content.inert = collapsed;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      content.hidden = collapsed;
      content.style.removeProperty("height");
      content.style.removeProperty("opacity");
      content.style.removeProperty("overflow");
      return;
    }
    content.hidden = false;
    content.style.height = `${startHeight}px`;
    content.style.overflow = "hidden";
    if (collapsed) {
      await Promise.all(fadeTargets.map((target, index) => playContentAnimation(
        target,
        [{ opacity: startOpacities[index] }, { opacity: 0 }],
        { duration: CARD_FADE_DURATION_MS, easing: "ease", fill: "forwards" },
      )));
      if (animationVersion !== collapseAnimationVersion) {
        return;
      }
      await playContentAnimation(
        content,
        [{ height: `${startHeight}px` }, { height: "0px" }],
        {
          duration: CARD_HEIGHT_DURATION_MS,
          easing: "ease",
          fill: "forwards",
        },
      );
    } else {
      const endHeight = content.scrollHeight;
      await Promise.all([
        playContentAnimation(
          content,
          [{ height: `${startHeight}px` }, { height: `${endHeight}px` }],
          { duration: CARD_HEIGHT_DURATION_MS, easing: "ease", fill: "forwards" },
        ),
        ...fadeTargets.map((target, index) => playContentAnimation(
          target,
          [{ opacity: startOpacities[index] }, { opacity: 1 }],
          { duration: CARD_FADE_DURATION_MS, easing: "ease", fill: "forwards" },
        )),
      ]);
    }
    if (animationVersion !== collapseAnimationVersion) {
      return;
    }
    cancelContentAnimations();
    content.style.removeProperty("height");
    content.style.removeProperty("opacity");
    content.style.removeProperty("overflow");
    content.hidden = collapsed;
  };

  const setCollapsed = (collapsed) => {
    card.classList.toggle("is-collapsed", collapsed);
    headingCopy.setAttribute("aria-expanded", String(!collapsed));
    setContentCollapsed(collapsed);
    cardCollapsedState[cardKey] = collapsed;
    saveCardCollapsedState();
  };
  const isInteractiveTarget = (target) => Boolean(
    target.closest("button, a, input, select, textarea, summary"),
  );

  heading.addEventListener("click", (event) => {
    if (isInteractiveTarget(event.target)) {
      event.stopPropagation();
      return;
    }
    event.stopPropagation();
    setCollapsed(!card.classList.contains("is-collapsed"));
  });
  heading.addEventListener("pointerdown", (event) => {
    if (isInteractiveTarget(event.target)) {
      event.stopPropagation();
    }
  });
  headingCopy.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    setCollapsed(!card.classList.contains("is-collapsed"));
  });
  card.addEventListener("click", (event) => {
    if (!card.classList.contains("is-collapsed") || isInteractiveTarget(event.target)) {
      return;
    }
    setCollapsed(false);
  });
}

function setupCollapsibleCards() {
  document.querySelectorAll("[data-collapsible-card]")
    .forEach(setupCollapsibleCard);
}
