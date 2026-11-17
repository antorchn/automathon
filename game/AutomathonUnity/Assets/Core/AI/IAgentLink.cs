namespace Automathon.AI
{
    /// <summary>
    /// Transport between the game and an agent that runs outside of it (the Python kit, a web server...).
    /// Each request ("Ping", or a JSON GameState) gets exactly one reply ("Pong", or a JSON AIMessage), in order.
    /// Every call is non-blocking, so the same code works in the browser (WebGL), where the game can't wait for
    /// the network on the main thread.
    /// </summary>
    public interface IAgentLink
    {
        /// <summary>Who answers, for the messages of the game (ex. "the agent at tcp://localhost:5555").</summary>
        string Description { get; }

        /// <summary>Sends a request. A link that isn't connected yet queues it until the connection opens.</summary>
        void Send(string message);

        /// <summary>Takes the reply to the oldest request still waiting, if it has arrived.</summary>
        bool TryReceive(out string reply);

        /// <summary>The connection failed or was closed: no reply will ever come.</summary>
        bool HasFailed { get; }

        /// <summary>
        /// Gives up on the requests still waiting (after a timeout): their late replies must never be taken as the
        /// reply to a later request.
        /// </summary>
        void DropPendingReplies();

        void Close();
    }
}
