using Automathon.AI;
using System;
using UnityEngine;

namespace Automathon.Game.Web
{
    /// <summary>
    /// What a player types in the menu to connect an AI:
    /// - ws://host:port/path or wss://...: an agent served over WebSocket (web app, PythonAI/webplay/server.py);
    /// - in the browser, a plain name: the agent of that name on the server of the page, origin + /ws/agent/name;
    /// - elsewhere, a port number: tcp://localhost:port (PythonAI/agents/run_my_agent.py, as before), or tcp://...
    /// </summary>
    public static class AgentAddress
    {
        public static bool IsBrowser => Application.platform == RuntimePlatform.WebGLPlayer;

        /// <summary>Label of the menu's text field.</summary>
        public static string FieldLabel => IsBrowser ? "AI AGENT :" : "AI TCP PORT :";

        /// <summary>Hint shown while the field is selected.</summary>
        public static string FieldHint => IsBrowser ? "ENTER THE AGENT NAME..." : "ENTER TCP PORT NUMBER...";

        /// <summary>The input provider of the agent at that address (connection still to be checked with a ping).
        /// Throws ArgumentException for an address this platform can't reach.</summary>
        public static AIInputProvider CreateProvider(string text)
        {
            text = (text ?? "").Trim();
            if (text.Length == 0)
                throw new ArgumentException("Empty agent address.");

            if (text.StartsWith("ws://", StringComparison.OrdinalIgnoreCase) || text.StartsWith("wss://", StringComparison.OrdinalIgnoreCase))
                return WebSocketProvider(text);

            if (IsBrowser)
                return WebSocketProvider(PageOrigin() + "/ws/agent/" + Uri.EscapeDataString(text));

#if !UNITY_WEBGL || UNITY_EDITOR
            if (text.StartsWith("tcp://", StringComparison.OrdinalIgnoreCase))
                return new AIInputProvider(text);
            if (int.TryParse(text, out int port) && port > 0 && port < 65536)
                return new AIInputProvider("tcp://localhost:" + port);
#endif
            throw new ArgumentException($"Invalid agent address: {text}");
        }

        private static AIInputProvider WebSocketProvider(string url)
            => new AIInputProvider(new WebSocketAgentLink(url)) { ResponseTimeout = WebSocketAgentLink.ResponseTimeout };

        /// <summary>ws(s)://host[:port] of the page that runs the game (browser only).</summary>
        public static string PageOrigin()
        {
            var page = new Uri(Application.absoluteURL);
            string scheme = page.Scheme == "https" ? "wss" : "ws";
            return $"{scheme}://{page.Authority}";
        }
    }
}
