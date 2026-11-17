using System;
using UnityEngine;

namespace Automathon.Game
{
    /// <summary>
    /// End-of-game slow motion, for the views only. The simulation knows nothing about it and keeps doing exactly one step
    /// per frame (the AIs still read its state, and it can't be slowed down without touching every timer of the engine).
    /// While it is active:
    /// - entity views stop following their entity and drift on with the velocity they had (see EntityView),
    /// - Time.timeScale slows down everything visual: the drifting views, particles, animations, shakes,
    /// - views of entities destroyed meanwhile stay on screen, and disappear silently when it ends.
    /// </summary>
    public static class SlowMotion
    {
        public static bool IsActive { get; private set; }

        /// <summary>Raised by End, once Time.timeScale is back to 1.</summary>
        public static event Action Ended;

        public static void Begin() => IsActive = true;

        public static void End()
        {
            if (!IsActive)
                return;

            IsActive = false;
            Time.timeScale = 1f;
            Ended?.Invoke();
        }
    }
}
