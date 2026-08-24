(() => {
  "use strict";

  const engine = window.gsap;
  const reduceMotionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const motionMedia = engine?.matchMedia();

  function reducedMotion() {
    return Boolean(reduceMotionQuery?.matches);
  }

  function targetsOf(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.filter(Boolean);
    if (typeof value.length === "number" && !value.nodeType) return Array.from(value).filter(Boolean);
    return [value];
  }

  function clearMotionStyles(targets) {
    const items = targetsOf(targets);
    if (!engine || !items.length) return;
    engine.set(items, { clearProps: "opacity,visibility,transform,willChange" });
  }

  function pageEnter(page) {
    if (!engine || !page) return null;
    engine.killTweensOf(page);
    if (reducedMotion()) {
      clearMotionStyles(page);
      return null;
    }
    return engine.fromTo(
      page,
      { autoAlpha: 0, y: 10, willChange: "transform, opacity" },
      {
        autoAlpha: 1,
        y: 0,
        duration: 0.24,
        ease: "power2.out",
        overwrite: "auto",
        clearProps: "opacity,visibility,transform,willChange",
      },
    );
  }

  function dialogOpen(overlay, card, onComplete) {
    if (!engine || !overlay || !card || reducedMotion()) {
      clearMotionStyles([overlay, card]);
      onComplete?.();
      return null;
    }
    engine.killTweensOf([overlay, card]);
    const timeline = engine.timeline({
      defaults: { ease: "power2.out" },
      onComplete,
    });
    timeline
      .fromTo(
        overlay,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.16, clearProps: "opacity,visibility" },
        0,
      )
      .fromTo(
        card,
        { autoAlpha: 0, y: 14, scale: 0.985, willChange: "transform, opacity" },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.22,
          clearProps: "opacity,visibility,transform,willChange",
        },
        0.025,
      );
    return timeline;
  }

  function dialogClose(overlay, card, onComplete) {
    if (!engine || !overlay || !card || reducedMotion()) {
      clearMotionStyles([overlay, card]);
      onComplete?.();
      return null;
    }
    engine.killTweensOf([overlay, card]);
    const finish = () => {
      clearMotionStyles([overlay, card]);
      onComplete?.();
    };
    const timeline = engine.timeline({
      defaults: { ease: "power1.in" },
      onComplete: finish,
    });
    timeline
      .to(
        card,
        { autoAlpha: 0, y: 8, scale: 0.99, duration: 0.14, willChange: "transform, opacity" },
        0,
      )
      .to(overlay, { autoAlpha: 0, duration: 0.14 }, 0.025);
    return timeline;
  }

  function taskItemsChanged(items) {
    if (!engine || reducedMotion()) {
      clearMotionStyles(items);
      return null;
    }
    const rows = targetsOf(items).slice(0, 20);
    if (!rows.length) return null;
    engine.killTweensOf(rows);
    const statusChips = rows.map((row) => row.querySelector?.(".task-status-chip")).filter(Boolean);
    const timeline = engine.timeline({ defaults: { ease: "power2.out" } });
    timeline.fromTo(
      rows,
      { autoAlpha: 0, y: 8, willChange: "transform, opacity" },
      {
        autoAlpha: 1,
        y: 0,
        duration: 0.22,
        stagger: 0.035,
        clearProps: "opacity,visibility,transform,willChange",
      },
      0,
    );
    if (statusChips.length) {
      timeline.fromTo(
        statusChips,
        { scale: 0.96 },
        { scale: 1, duration: 0.18, stagger: 0.035, clearProps: "transform" },
        0.06,
      );
    }
    return timeline;
  }

  if (engine) {
    document.documentElement.classList.add("motion-ready");
    motionMedia.add("(prefers-reduced-motion: reduce)", () => {
      document.documentElement.classList.add("motion-reduced");
      return () => document.documentElement.classList.remove("motion-reduced");
    });
  }

  window.PlatformMotion = Object.freeze({
    available: Boolean(engine),
    reducedMotion,
    pageEnter,
    dialogOpen,
    dialogClose,
    taskItemsChanged,
  });
})();
