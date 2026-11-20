using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.Build.Reporting;

namespace AutomathonEditor
{
    /// <summary>
    /// Compilation du jeu en ligne de commande, appelée par tools/build.py :
    /// <code>
    /// Unity -batchmode -quit -projectPath game/AutomathonUnity -buildTarget Win64
    ///       -executeMethod AutomathonEditor.AutomathonBuild.Build
    ///       -automathonTarget windows -automathonOutput build/game/windows -automathonVersion 0.5.0
    /// </code>
    /// Une exception fait échouer Unity avec un code de sortie non nul.
    /// </summary>
    public static class AutomathonBuild
    {
        public static void Build()
        {
            string target = RequiredArg("-automathonTarget");
            string output = RequiredArg("-automathonOutput");
            string version = OptionalArg("-automathonVersion");

            BuildTarget buildTarget;
            BuildTargetGroup targetGroup = BuildTargetGroup.Standalone;
            string executable;
            string windowsArchitecture = null;  // valeur de UnityEditor.Build.OSArchitecture
            switch (target)
            {
                case "windows":
                    buildTarget = BuildTarget.StandaloneWindows64; executable = "AutomathonGame.exe";
                    windowsArchitecture = "x64";
                    break;
                case "windows-arm64":
                    buildTarget = BuildTarget.StandaloneWindows64; executable = "AutomathonGame.exe";
                    windowsArchitecture = "ARM64";
                    break;
                case "linux": buildTarget = BuildTarget.StandaloneLinux64; executable = "AutomathonGame.x86_64"; break;
                case "macos": buildTarget = BuildTarget.StandaloneOSX; executable = "AutomathonGame.app"; break;
                // Unity écrit index.html et Build/ dans le dossier donné
                case "webgl": buildTarget = BuildTarget.WebGL; targetGroup = BuildTargetGroup.WebGL; executable = ""; break;
                default:
                    throw new ArgumentException(
                        $"Cible inconnue : {target} (windows, windows-arm64, linux, macos ou webgl)");
            }

            string[] scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
            if (scenes.Length == 0)
                throw new InvalidOperationException("Aucune scène activée dans les Build Settings.");

            // Le numéro de version n'est appliqué que le temps de la compilation, pour ne pas modifier
            // ProjectSettings.asset à chaque build.
            string previousVersion = PlayerSettings.bundleVersion;
            if (!string.IsNullOrEmpty(version))
                PlayerSettings.bundleVersion = version;
            using WebGLSettingsScope webGLSettings = target == "webgl" ? new WebGLSettingsScope() : null;
            using WindowsArchitectureScope architecture =
                windowsArchitecture != null ? new WindowsArchitectureScope(windowsArchitecture) : null;
            try
            {
                BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
                {
                    scenes = scenes,
                    locationPathName = executable.Length == 0 ? output : Path.Combine(output, executable),
                    target = buildTarget,
                    targetGroup = targetGroup,
                    options = BuildOptions.None,
                });
                BuildSummary summary = report.summary;
                UnityEngine.Debug.Log($"[AutomathonBuild] {target} : {summary.result}, {summary.totalErrors} erreur(s), "
                                      + $"{summary.totalSize / (1024 * 1024)} Mo, {summary.totalTime}");
                if (summary.result != BuildResult.Succeeded)
                    throw new Exception($"Échec de la compilation {target} : {summary.result}");
            }
            finally
            {
                PlayerSettings.bundleVersion = previousVersion;
            }
        }

        /// <summary>
        /// Réglages du jeu WebGL, appliqués le temps de la compilation (comme la version) :
        /// - fichiers compressés en gzip avec décompression de secours dans la page : le jeu marche sur n'importe quel
        ///   serveur HTTP, plus vite si le serveur envoie Content-Encoding: gzip (PythonAI/webplay/server.py le fait) ;
        /// - WebGL2 seulement (pas de WebGPU : les effets VFX Graph sont remplacés par ParticleFallback) ;
        /// - exceptions gérées sans traces complètes, pour un code plus petit.
        /// </summary>
        private sealed class WebGLSettingsScope : IDisposable
        {
            private readonly WebGLCompressionFormat compression = PlayerSettings.WebGL.compressionFormat;
            private readonly bool decompressionFallback = PlayerSettings.WebGL.decompressionFallback;
            private readonly WebGLExceptionSupport exceptionSupport = PlayerSettings.WebGL.exceptionSupport;
            private readonly bool dataCaching = PlayerSettings.WebGL.dataCaching;

            public WebGLSettingsScope()
            {
                PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
                PlayerSettings.WebGL.decompressionFallback = true;
                PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
                PlayerSettings.WebGL.dataCaching = true;
            }

            public void Dispose()
            {
                PlayerSettings.WebGL.compressionFormat = compression;
                PlayerSettings.WebGL.decompressionFallback = decompressionFallback;
                PlayerSettings.WebGL.exceptionSupport = exceptionSupport;
                PlayerSettings.WebGL.dataCaching = dataCaching;
            }
        }

        /// <summary>
        /// Architecture du jeu Windows (x64 ou ARM64), appliquée le temps de la compilation : réglage
        /// UnityEditor.WindowsStandalone.UserBuildSettings.architecture du module Windows de Unity, lu par réflexion
        /// pour que ce script compile aussi sur un éditeur sans ce module.
        /// </summary>
        private sealed class WindowsArchitectureScope : IDisposable
        {
            private readonly PropertyInfo property;
            private readonly object previous;

            public WindowsArchitectureScope(string architecture)
            {
                Type settings = AppDomain.CurrentDomain.GetAssemblies()
                    .Select(a => a.GetType("UnityEditor.WindowsStandalone.UserBuildSettings"))
                    .FirstOrDefault(t => t != null)
                    ?? throw new InvalidOperationException(
                        "Module Windows de Unity introuvable (UnityEditor.WindowsStandalone.UserBuildSettings).");
                property = settings.GetProperty("architecture", BindingFlags.Public | BindingFlags.Static)
                    ?? throw new InvalidOperationException("Réglage UserBuildSettings.architecture introuvable.");
                previous = property.GetValue(null);
                property.SetValue(null, Enum.Parse(property.PropertyType, architecture));
                UnityEngine.Debug.Log($"[AutomathonBuild] architecture Windows : {property.GetValue(null)}");
            }

            public void Dispose() => property.SetValue(null, previous);
        }

        private static string OptionalArg(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, name);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
        }

        private static string RequiredArg(string name)
            => OptionalArg(name) ?? throw new ArgumentException($"Argument manquant : {name}");
    }
}
