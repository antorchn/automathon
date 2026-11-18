using Automathon.Engine;
using Automathon.Game;
using System;
using System.Collections.Generic;

namespace Automathon.AI
{
    /// <summary>
    /// A match recorded on the Headless (PythonAI/webplay/replay.py), replayed frame for frame by the visual game: the
    /// simulation is deterministic (fixed point), so the map seed, the rules and the actions of both ships are enough.
    /// JSON, same field names as here.
    /// </summary>
    public class MatchReplay
    {
        public const int CurrentVersion = 1;

        public int Version = CurrentVersion;

        /// <summary>
        /// Version of the game that recorded the match (VERSION of the repository, for example "1.0.0"), null = unknown.
        /// The web game refuses a replay recorded by another version: its simulation may differ (see Web/WebBridge).
        /// </summary>
        public string GameVersion;

        /// <summary>Map seed and wall density, as in ResetOptions (null density = default density).</summary>
        public int Seed;
        public double? MapDensity;

        /// <summary>Rules of the match (null = default rules).</summary>
        public GameRules Rules;

        /// <summary>Names shown for the ship on the left (green) and on the right (red).</summary>
        public string LeftName;
        public string RightName;

        /// <summary>The actions, in order: each one is held for Frames frames, like an AIMessage step of the Headless.</summary>
        public List<ReplayStep> Steps = new List<ReplayStep>();

        /// <summary>End of the match as recorded on the Headless (to check a replay), may be null.</summary>
        public ReplayResult Result;

        /// <summary>
        /// Match played live: its steps are still being added as the platform plays them (WebBridge.AppendSteps). The
        /// replay then waits for the next step instead of ending, until this goes back to false (WebBridge.EndLive).
        /// </summary>
        [Newtonsoft.Json.JsonIgnore]
        public bool AwaitingSteps;

        /// <summary>Frames covered by the steps (the match may end earlier, when a ship dies).</summary>
        public int TotalFrames
        {
            get
            {
                int total = 0;
                foreach (ReplayStep step in Steps)
                    total += step.ClampedFrames;
                return total;
            }
        }

        public void Validate()
        {
            if (Version != CurrentVersion)
                throw new FormatException($"Replay version {Version}, this game reads version {CurrentVersion}.");
            if (Steps == null || (Steps.Count == 0 && !AwaitingSteps))
                throw new FormatException("The replay has no steps.");
        }

        /// <summary>
        /// Starts the match of the replay (rules, map, ships) and returns the input providers of the left and right ships,
        /// which play the recorded actions on their own as GameplayManager.Update runs.
        /// </summary>
        public (ReplayInputProvider left, ReplayInputProvider right) Start()
        {
            Validate();
            var left = new ReplayInputProvider(this, ReplaySide.Left);
            var right = new ReplayInputProvider(this, ReplaySide.Right);
            GameRules.Set(Rules); // before the reset: ships read their max health from the rules
            GameplayManager.Reset(left, right, Seed, MapDensity);
            return (left, right);
        }

        /// <summary>
        /// Plays the whole replay without display (Headless check: `Headless --replay file.json`) and returns how the
        /// match ended. Same frame loop as TrainingManager.Simulate.
        /// </summary>
        public ReplayResult Run()
        {
            Start();
            int totalFrames = TotalFrames;
            while (GameplayManager.State == GameplayManager.GameplayState.Game && GameplayManager.Frame < totalFrames)
            {
                GameplayManager.Update();
                if (GameplayManager.Tank1.Health.IsDead || GameplayManager.Tank2.Health.IsDead)
                    break;
            }
            return CurrentResult();
        }

        /// <summary>State of the replayed match, seen from the left ship.</summary>
        public static ReplayResult CurrentResult()
        {
            GameState state = GameplayManager.GetState(GameplayManager.Tank1.InputProvider);
            return new ReplayResult
            {
                Frame = GameplayManager.Frame,
                Outcome = state.Outcome,
                LeftHealth = GameplayManager.Tank1.Health.CurrentHealth,
                RightHealth = GameplayManager.Tank2.Health.CurrentHealth,
                LeftPosition = state.SelfShip?.Position,
                RightPosition = state.EnemyShip?.Position,
            };
        }
    }

    public class ReplayStep
    {
        /// <summary>Frames played with these actions (Headless: AIMessage.Frames, between 1 and 600).</summary>
        public int Frames;
        public AIAction Left;
        public AIAction Right;

        internal int ClampedFrames => Math.Max(1, Math.Min(Frames, 600));
    }

    public class ReplayResult
    {
        public int Frame;
        /// <summary>For the ship on the left: "Win", "Loss", "Draw" or "Ongoing" (replay stopped before the end).</summary>
        public string Outcome;
        public Fixed LeftHealth;
        public Fixed RightHealth;
        /// <summary>Last position of each ship still alive (null once destroyed): exact to the bit, a replay that drifts
        /// even slightly shows here.</summary>
        public FixedVector2? LeftPosition;
        public FixedVector2? RightPosition;

        public bool SameAs(ReplayResult other)
            => other != null && Frame == other.Frame && Outcome == other.Outcome
               && LeftHealth == other.LeftHealth && RightHealth == other.RightHealth
               && LeftPosition == other.LeftPosition && RightPosition == other.RightPosition;

        public override string ToString()
            => $"frame {Frame}, {Outcome} for the left ship, health {LeftHealth} / {RightHealth}, "
               + $"positions {Show(LeftPosition)} / {Show(RightPosition)}";

        private static string Show(FixedVector2? position)
            => position.HasValue ? $"({position.Value.X}, {position.Value.Y})" : "destroyed";
    }

    public enum ReplaySide { Left, Right }

    /// <summary>Plays one side of a MatchReplay: at the first frame of each step, takes that step's action.</summary>
    public class ReplayInputProvider : AIInputProvider
    {
        private readonly MatchReplay replay;
        private readonly ReplaySide side;
        private int nextStep;
        private int nextStepFrame;

        public ReplayInputProvider(MatchReplay replay, ReplaySide side)
        {
            this.replay = replay;
            this.side = side;
        }

        /// <summary>All the recorded actions have been played and the last one is over (and no more are coming).</summary>
        public bool IsFinished => !replay.AwaitingSteps && OutOfSteps;

        private bool OutOfSteps => nextStep >= replay.Steps.Count && GameplayManager.Frame >= nextStepFrame;

        /// <summary>A live match whose next step hasn't arrived yet holds its frame (see GameplayManager.InputsReady).</summary>
        public override bool IsReadyForFrame() => !(replay.AwaitingSteps && OutOfSteps);

        public override void BeforeFrame()
        {
            if (nextStep >= replay.Steps.Count || GameplayManager.Frame != nextStepFrame)
                return;

            ReplayStep step = replay.Steps[nextStep];
            UpdateFromAction(Copy(side == ReplaySide.Left ? step.Left : step.Right));
            nextStepFrame += step.ClampedFrames;
            nextStep++;
        }

        // UpdateFromAction normalizes the action in place: the replay itself stays as loaded (it can be played again)
        private static AIAction Copy(AIAction action) => action == null ? null : new AIAction
        {
            MovingDirection = action.MovingDirection,
            AimingDirection = action.AimingDirection,
            MachineGun = action.MachineGun,
            Missile = action.Missile,
            Shield = action.Shield,
            Dash = action.Dash,
        };
    }
}
