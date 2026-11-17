using Automathon.Game;
using UnityEditor;

namespace AutomathonEditor
{
    /// <summary>Shows in the editor the explosions of the browser build (ParticleFallback) instead of the VFX Graph ones.</summary>
    public static class ParticleFallbackMenu
    {
        private const string MenuPath = "Automathon/Particle fallback (WebGL look)";

        [MenuItem(MenuPath)]
        private static void Toggle()
            => EditorPrefs.SetBool(ParticleFallback.EditorPrefKey, !EditorPrefs.GetBool(ParticleFallback.EditorPrefKey, false));

        [MenuItem(MenuPath, true)]
        private static bool ToggleValidate()
        {
            Menu.SetChecked(MenuPath, EditorPrefs.GetBool(ParticleFallback.EditorPrefKey, false));
            return true;
        }
    }
}
