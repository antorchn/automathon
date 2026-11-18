using Automathon.AI;
using Newtonsoft.Json;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Networking;

namespace Automathon.Game.Web
{
    /// <summary>
    /// Entry points for the web page that hosts the game (web app of the hackathon, PythonAI/webplay/README.md):
    /// - page URL with ?replay=URL: plays that replay (JSON MatchReplay) as soon as the game is loaded;
    /// - JavaScript, once the page got the event automathon:ready: unityInstance.SendMessage("WebBridge", method, value)
    ///   with the public methods below (a string, a number or nothing, as each one takes);
    /// - events for the page, on window: automathon:ready, matchstart, matchend, screencovered, sequenceend, error
    ///   (Plugins/WebGL/AutomathonWeb.jslib); outside the browser they are written to the log ("[Page] ...");
    /// - desktop game: AutomathonGame -replay match.json (file path or URL).
    /// Created by code at startup (no change to the scene), on every platform.
    /// </summary>
    public class WebBridge : MonoBehaviour
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern void AutomathonPage_Notify(string type, string detailJson);
#endif

        private bool ready;
        private readonly List<Action> pending = new List<Action>();

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void CreateBridge()
        {
            var bridge = new GameObject("WebBridge");
            DontDestroyOnLoad(bridge);
            bridge.AddComponent<WebBridge>();
        }

        private IEnumerator Start()
        {
            yield return null; // WorldView and the menus are set up

            WorldView world = WorldView.Instance;
            world.MatchStarted += OnMatchStarted;
            world.MatchEnded += OnMatchEnded;
            world.ScreenCovered += OnScreenCovered;
            world.EndSequenceOver += OnEndSequenceOver;

            ready = true;
            Notify("ready", new Dictionary<string, object> { ["version"] = Application.version });
            foreach (Action action in pending)
                action();
            pending.Clear();

            string replayUrl = AgentAddress.IsBrowser ? QueryParameter(Application.absoluteURL, "replay") : CommandLineReplay();
            if (!string.IsNullOrEmpty(replayUrl))
                PlayReplayUrl(replayUrl);
        }

        private void OnDestroy()
        {
            WorldView world = WorldView.Instance;
            if (!ready || world == null)
                return;
            world.MatchStarted -= OnMatchStarted;
            world.MatchEnded -= OnMatchEnded;
            world.ScreenCovered -= OnScreenCovered;
            world.EndSequenceOver -= OnEndSequenceOver;
        }

        // Calls made before the game is set up wait for it (they would otherwise be undone by the menus starting)
        private void WhenReady(Action action)
        {
            if (ready)
                action();
            else
                pending.Add(action);
        }

        // ---- Replays ------------------------------------------------------------------------------------------------

        /// <summary>Plays a replay given as JSON text (MatchReplay), from its start.</summary>
        public void PlayReplay(string json) => WhenReady(() => Play(json, live: false));

        /// <summary>Downloads a replay (absolute URL, or relative to the page) then plays it.</summary>
        public void PlayReplayUrl(string url) => WhenReady(() => StartCoroutine(DownloadAndPlay(url)));

        /// <summary>
        /// Starts a match played live on the platform: a MatchReplay whose steps may still be empty. The game plays the
        /// steps as they arrive with AppendSteps and waits for the next one, until EndLive.
        /// </summary>
        public void PlayLive(string json) => WhenReady(() => Play(json, live: true));

        /// <summary>Adds steps to the live match: a JSON array of steps (same format as MatchReplay.Steps).</summary>
        public void AppendSteps(string json) => WhenReady(() =>
        {
            MatchReplay replay = WorldView.Instance.CurrentReplay;
            if (replay == null || !replay.AwaitingSteps)
            {
                Error("not-live", "AppendSteps: no live match in progress (PlayLive first).");
                return;
            }
            try
            {
                replay.Steps.AddRange(JsonConvert.DeserializeObject<List<ReplayStep>>(json));
            }
            catch (Exception e)
            {
                Error("invalid-steps", "AppendSteps: invalid steps: " + e.Message);
            }
        });

        /// <summary>
        /// No more steps for the live match: it ends with its last step (or before, when a ship dies). resultJson: the
        /// end of the match on the platform (MatchReplay.Result) to check the replay against, or "" when unknown.
        /// </summary>
        public void EndLive(string resultJson) => WhenReady(() =>
        {
            MatchReplay replay = WorldView.Instance.CurrentReplay;
            if (replay == null || !replay.AwaitingSteps)
            {
                Error("not-live", "EndLive: no live match in progress.");
                return;
            }
            if (!string.IsNullOrEmpty(resultJson))
            {
                try
                {
                    replay.Result = JsonConvert.DeserializeObject<ReplayResult>(resultJson);
                }
                catch (Exception e)
                {
                    Error("invalid-result", "EndLive: invalid result, not checked: " + e.Message);
                }
            }
            replay.AwaitingSteps = false;
        });

        private void Play(string json, bool live)
        {
            MatchReplay replay;
            try
            {
                replay = JsonConvert.DeserializeObject<MatchReplay>(json);
                if (replay == null)
                    throw new FormatException("empty replay");
                replay.Steps ??= new List<ReplayStep>();
                replay.AwaitingSteps = live;
                replay.Validate();
            }
            catch (Exception e)
            {
                Error("invalid-replay", "Invalid replay: " + e.Message);
                return;
            }

            // The simulation of another version may differ: the match shown would not be the one played. The editor
            // has no version of its own (it is set when building, from VERSION).
            if (!Application.isEditor && !string.IsNullOrEmpty(replay.GameVersion) && replay.GameVersion != Application.version)
            {
                Error("version", $"Replay recorded with version {replay.GameVersion} of the game, this is version "
                                 + $"{Application.version}: not played, the match could differ.");
                return;
            }

            WorldView.Instance.StartReplay(replay);
        }

        private IEnumerator DownloadAndPlay(string url)
        {
            if (!string.IsNullOrEmpty(Application.absoluteURL) && !url.Contains("://"))
                url = new Uri(new Uri(Application.absoluteURL), url).ToString();

            using UnityWebRequest request = UnityWebRequest.Get(url);
            yield return request.SendWebRequest();
            if (request.result != UnityWebRequest.Result.Success)
            {
                Error("download", $"Could not download {url}: {request.error}");
                yield break;
            }
            Play(request.downloadHandler.text, live: false);
        }

        // ---- Display ------------------------------------------------------------------------------------------------

        /// <summary>Pauses the match (simulation, views, sound). Resume to go on.</summary>
        public void Pause() => WhenReady(() => WorldView.Instance.Paused = true);

        public void Resume() => WhenReady(() => WorldView.Instance.Paused = false);

        /// <summary>Speed of the match: 1 = real time, 2 = twice as fast... between 0.1 and 8.</summary>
        public void SetSpeed(float speed) => WhenReady(() => WorldView.Instance.Speed = speed);

        /// <summary>1: no sound, 0: sound (default).</summary>
        public void SetMuted(float muted) => WhenReady(() => AudioListener.volume = muted != 0 ? 0f : 1f);

        /// <summary>
        /// 1: end screen of the game after the light explosion (default). 0: none, the light fades away to an empty
        /// arena, for a page that shows its own result (on automathon:screencovered).
        /// </summary>
        public void SetEndScreen(float shown) => WhenReady(() => WorldView.Instance.ShowEndScreen = shown != 0);

        // ---- Events for the page ------------------------------------------------------------------------------------

        // Details are dictionaries, not anonymous types: those have no namespace for link.xml, and the browser build
        // (IL2CPP) could strip the getters that Newtonsoft.Json reads by reflection

        private static void OnMatchStarted(MatchReplay replay)
        {
            Notify("matchstart", new Dictionary<string, object>
            {
                ["replay"] = replay != null,
                ["live"] = replay?.AwaitingSteps ?? false,
                ["left"] = replay?.LeftName,
                ["right"] = replay?.RightName,
                ["seed"] = replay?.Seed,
                ["frames"] = replay?.TotalFrames, // of the steps received so far, for a live match
            });
        }

        private static void OnMatchEnded(ReplayResult result, bool? sameAsRecorded)
        {
            Notify("matchend", new Dictionary<string, object>
            {
                ["frame"] = result.Frame,
                ["outcome"] = result.Outcome, // seen from the left ship
                ["winner"] = result.Outcome switch { "Win" => "left", "Loss" => "right", "Draw" => "draw", _ => null },
                ["leftHealth"] = (double)result.LeftHealth,
                ["rightHealth"] = (double)result.RightHealth,
                ["replay"] = WorldView.Instance.IsReplaying,
                ["sameAsRecorded"] = sameAsRecorded,
            });
        }

        private static void OnScreenCovered() => Notify("screencovered");

        private static void OnEndSequenceOver() => Notify("sequenceend");

        private static void Error(string code, string message)
        {
            UnityEngine.Debug.LogError("[WebBridge] " + message);
            Notify("error", new Dictionary<string, object> { ["code"] = code, ["message"] = message });
        }

        /// <summary>Event "automathon:type" on the window of the page, detail = the dictionary as JSON (or null).</summary>
        private static void Notify(string type, Dictionary<string, object> detail = null)
        {
            string json = detail == null ? null : JsonConvert.SerializeObject(detail);
#if UNITY_WEBGL && !UNITY_EDITOR
            AutomathonPage_Notify(type, json);
#else
            UnityEngine.Debug.Log($"[Page] automathon:{type} {json}");
#endif
        }

        // ---- Helpers ------------------------------------------------------------------------------------------------

        private static string CommandLineReplay()
        {
            string[] args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, "-replay");
            if (i < 0 || i + 1 >= args.Length)
                return null;
            string value = args[i + 1];
            return value.Contains("://") ? value : new Uri(System.IO.Path.GetFullPath(value)).AbsoluteUri;
        }

        /// <summary>Value of name in the query string of url (decoded), or null.</summary>
        public static string QueryParameter(string url, string name)
        {
            if (string.IsNullOrEmpty(url))
                return null;
            int start = url.IndexOf('?');
            if (start < 0)
                return null;
            int end = url.IndexOf('#', start);
            string query = end < 0 ? url.Substring(start + 1) : url.Substring(start + 1, end - start - 1);
            foreach (string pair in query.Split('&'))
            {
                int eq = pair.IndexOf('=');
                string key = Uri.UnescapeDataString((eq < 0 ? pair : pair.Substring(0, eq)).Replace('+', ' '));
                if (key == name)
                    return eq < 0 ? "" : Uri.UnescapeDataString(pair.Substring(eq + 1).Replace('+', ' '));
            }
            return null;
        }
    }
}
