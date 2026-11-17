using Automathon.AI;
using System;
using System.Collections.Concurrent;
#if UNITY_WEBGL && !UNITY_EDITOR
using System.Runtime.InteropServices;
#else
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
#endif

namespace Automathon.Game.Web
{
    /// <summary>
    /// Agent reached through a WebSocket (ws:// or wss://), served by PythonAI/webplay/server.py or the web app of the
    /// hackathon: text messages, same content as the ZeroMQ link ("Ping" -> "Pong", JSON GameState -> JSON AIMessage).
    /// In the browser, the page's WebSocket (Plugins/WebGL/AutomathonWeb.jslib); elsewhere (editor, desktop builds),
    /// System.Net.WebSockets.
    /// </summary>
    public class WebSocketAgentLink : IAgentLink
    {
        /// <summary>A remote agent needs more time than a local one (network, first call of a model).</summary>
        public static readonly TimeSpan ResponseTimeout = TimeSpan.FromSeconds(5);

        public string Url { get; }

        public string Description => $"the agent at {Url}";

        // Requests sent whose reply hasn't been taken, and late replies to throw away (see DropPendingReplies).
        // The replies of a WebSocket arrive in the order of the requests.
        private int pendingReplies;
        private int repliesToDrop;

        public WebSocketAgentLink(string url)
        {
            Url = url;
            Open();
        }

        public void Send(string message)
        {
            if (HasFailed)
                return;
            SendRaw(message);
            pendingReplies++;
        }

        public bool TryReceive(out string reply)
        {
            while (ReceiveRaw(out reply))
            {
                if (pendingReplies > 0)
                    pendingReplies--;
                if (repliesToDrop > 0)
                {
                    repliesToDrop--;
                    continue;
                }
                return true;
            }
            reply = null;
            return false;
        }

        public void DropPendingReplies()
        {
            repliesToDrop = pendingReplies;
        }

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern int AgentSocket_Open(string url);
        [DllImport("__Internal")] private static extern void AgentSocket_Send(int id, string message);
        [DllImport("__Internal")] private static extern string AgentSocket_Receive(int id);
        [DllImport("__Internal")] private static extern int AgentSocket_State(int id);
        [DllImport("__Internal")] private static extern void AgentSocket_Close(int id);

        private int socketId;

        private void Open() => socketId = AgentSocket_Open(Url);
        public bool HasFailed => socketId == 0 || AgentSocket_State(socketId) == 2;
        private void SendRaw(string message) => AgentSocket_Send(socketId, message);

        private bool ReceiveRaw(out string message)
        {
            message = socketId == 0 ? null : AgentSocket_Receive(socketId);
            return message != null;
        }

        public void Close()
        {
            if (socketId != 0)
                AgentSocket_Close(socketId);
            socketId = 0;
        }
#else
        private ClientWebSocket socket;
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private readonly ConcurrentQueue<string> incoming = new ConcurrentQueue<string>();
        private readonly BlockingCollection<string> outgoing = new BlockingCollection<string>();
        private volatile bool failed;

        private void Open()
        {
            socket = new ClientWebSocket();
            Uri uri;
            try
            {
                uri = new Uri(Url);
            }
            catch (UriFormatException)
            {
                failed = true;
                return;
            }
            Task.Run(() => Run(uri));
        }

        private async Task Run(Uri uri)
        {
            try
            {
                await socket.ConnectAsync(uri, cancellation.Token);
                Task sender = Task.Run(async () =>
                {
                    try
                    {
                        foreach (string message in outgoing.GetConsumingEnumerable(cancellation.Token))
                            await socket.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(message)),
                                                   WebSocketMessageType.Text, true, cancellation.Token);
                    }
                    catch (Exception)
                    {
                        // Lost connection while sending (or Close): no reply will come, so the game must know at once
                        // instead of waiting for the timeout at every decision. Also ends the receive loop below.
                        failed = true;
                        cancellation.Cancel();
                    }
                });

                var buffer = new byte[64 * 1024];
                var text = new StringBuilder();
                while (socket.State == WebSocketState.Open)
                {
                    WebSocketReceiveResult result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), cancellation.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                        break;
                    text.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                    if (result.EndOfMessage)
                    {
                        incoming.Enqueue(text.ToString());
                        text.Clear();
                    }
                }
            }
            catch (Exception)
            {
                // Refused, lost or closed connection: HasFailed tells the game
            }
            failed = true;
        }

        public bool HasFailed => failed && incoming.IsEmpty;
        private void SendRaw(string message) => outgoing.Add(message);
        private bool ReceiveRaw(out string message) => incoming.TryDequeue(out message);

        public void Close()
        {
            failed = true;
            cancellation.Cancel();
            socket?.Dispose();
            socket = null;
        }
#endif
    }
}
