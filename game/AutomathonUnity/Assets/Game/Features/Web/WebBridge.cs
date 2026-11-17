using Automathon.AI;
using Newtonsoft.Json;
using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

namespace Automathon.Game.Web
{
    /// <summary>
    /// Entry points for the web page that hosts the game (web app of the hackathon, PythonAI/webplay/server.py):
    /// - page URL with ?replay=URL: plays that replay (JSON MatchReplay) as soon as the game is loaded;
    /// - JavaScript: unityInstance.SendMessage("WebBridge", "PlayReplay", json) or ("WebBridge", "PlayReplayUrl", url).
    /// - desktop game: AutomathonGame -replay match.json (file path or URL).
    /// Created by code at startup (no change to the scene), on every platform.
    /// </summary>
    public class WebBridge : MonoBehaviour
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void CreateBridge()
        {
            var bridge = new GameObject("WebBridge");
            DontDestroyOnLoad(bridge);
            bridge.AddComponent<WebBridge>();
        }

        private IEnumerator Start()
        {
            string replayUrl = AgentAddress.IsBrowser ? QueryParameter(Application.absoluteURL, "replay") : CommandLineReplay();
            if (string.IsNullOrEmpty(replayUrl))
                yield break;
            yield return null; // WorldView and the menus are set up
            PlayReplayUrl(replayUrl);
        }

        /// <summary>Plays a replay given as JSON text (MatchReplay).</summary>
        public void PlayReplay(string json)
        {
            MatchReplay replay;
            try
            {
                replay = JsonConvert.DeserializeObject<MatchReplay>(json);
                replay.Validate();
            }
            catch (Exception e)
            {
                UnityEngine.Debug.LogError("[Replay] Invalid replay: " + e.Message);
                return;
            }
            WorldView.Instance.StartReplay(replay);
        }

        private static string CommandLineReplay()
        {
            string[] args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, "-replay");
            if (i < 0 || i + 1 >= args.Length)
                return null;
            string value = args[i + 1];
            return value.Contains("://") ? value : new Uri(System.IO.Path.GetFullPath(value)).AbsoluteUri;
        }

        /// <summary>Downloads a replay (absolute URL, or relative to the page) then plays it.</summary>
        public void PlayReplayUrl(string url) => StartCoroutine(DownloadAndPlay(url));

        private IEnumerator DownloadAndPlay(string url)
        {
            if (!string.IsNullOrEmpty(Application.absoluteURL) && !url.Contains("://"))
                url = new Uri(new Uri(Application.absoluteURL), url).ToString();

            using UnityWebRequest request = UnityWebRequest.Get(url);
            yield return request.SendWebRequest();
            if (request.result != UnityWebRequest.Result.Success)
            {
                UnityEngine.Debug.LogError($"[Replay] Could not download {url}: {request.error}");
                yield break;
            }
            PlayReplay(request.downloadHandler.text);
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
