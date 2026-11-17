using Automathon.AI;
using Automathon.Engine;
using Automathon.Game.Input;
using Automathon.Game.View;
using Newtonsoft.Json;
using System;
using System.Collections;
using System.IO;
using UnityEngine;

namespace Automathon.Game
{
    public class WorldView : MonoBehaviour
    {
        private enum LobbyStates { Logo, Input, End }


        public static WorldView Instance;
        private LobbyStates LobbyState;

        [SerializeField] private EntityViewRegistry entityViewRegistry;

        [Header("UI")]
        [SerializeField] private UnityEngine.UI.Button playButton;
        [SerializeField] private GameObject inputTakingMenu;
        [SerializeField] private InputTaking inputTaking;
        [SerializeField] private EndScreen endCard;

        [Header("End of game")]
        [Tooltip("Speed of the end-of-game slow motion, 1 = normal speed")]
        [SerializeField, Range(0.01f, 1f)] private float slowMotionScale = 0.08f;
        [Tooltip("Real seconds to go from normal speed to slowMotionScale")]
        [SerializeField] private float slowMotionRampDuration = 0.35f;

        private bool isEndOfGameSequenceRunning;

        public InputProvider[] InputProviders;

        private bool subbedToSpawnEntityView;

        // Replay being played (MatchReplay, from the web page: see Web/WebBridge), null otherwise
        private MatchReplay replay;
        private ReplayInputProvider replayLeft;
        private bool replayEndLogged;

        // Browser only: simulation frames owed to the real time (the page draws at the screen's refresh rate)
#pragma warning disable CS0414 // read only in the browser build (FramesToSimulate)
        private float frameAccumulator;
#pragma warning restore CS0414
        private const int MaxFramesPerUpdate = 4;

        /// <summary>A replay is being played.</summary>
        public bool IsReplaying => replay != null;

        private void Awake()
        {
            if (Instance != null)
            {
                Destroy(this);
                return;
            }

            Instance = this;

            Debug.LogEvent += DebugForward;
            Debug.LogErrorEvent += DebugErrorForward;

            GameplayManager.Initialize();
            GameplayManager.EntitySpawned += SpawnEntityViewFromDict;
            GameplayManager.GameEnded += OnEndGame;
            GameplayManager.GameDrawn += OnDrawGame;

            InputProviders = new InputProvider[] { null, null };

            // Safe zone outline (only drawn when the rules enable a zone)
            new GameObject("SafeZone").AddComponent<ZoneView>();

            subbedToSpawnEntityView = true;

#if UNITY_WEBGL && !UNITY_EDITOR
            // The browser paces the page (requestAnimationFrame): Update keeps the 60 simulation frames per second itself
            Application.targetFrameRate = -1;
#else
            Application.targetFrameRate = GameplayConstants.FRAMERATE;
            QualitySettings.vSyncCount = 0;
#endif

            LobbyState = LobbyStates.Logo;

            playButton.gameObject.SetActive(true);
            playButton.onClick.AddListener(OnStartButtonClicked);
        }

        private void OnStartButtonClicked()
        {
            SoundManager.instance.PlaySound("MenuButton");
            playButton.enabled = false;
            StartCoroutine(ScreenGlitchTransition(() =>
            {
                playButton.gameObject.SetActive(false);
                inputTakingMenu.SetActive(true);
                inputTaking.DeactivateMenu();
            }, () =>
            {
                LobbyState = LobbyStates.Input;
                inputTaking.ActivateMenu();
            }));
        }

        private IEnumerator ScreenGlitchTransition(Action middleOfTransitionChanges, Action endOfTransitionChanges)
        {
            float transitionDuration = ScreenGlitch.instance.menuTransition.duration;
            ScreenGlitch.TriggerMenuTransition();
            yield return new WaitForSeconds(transitionDuration / 2f);
            middleOfTransitionChanges();
            yield return new WaitForSeconds(transitionDuration / 2f);
            endOfTransitionChanges();
        }

        private void SpawnEntityViewFromDict(Entity entity)
        {
            // Nothing new appears during the end-of-game slow motion: the views on screen have stopped following the
            // simulation, which goes on at full speed (a bullet's impact would pop up far ahead of the slowly drifting bullet)
            if (SlowMotion.IsActive)
                return;

            EntityView entityViewPrefab = entityViewRegistry.GetPrefabFor(entity.GetType());

            if (entityViewPrefab == null)
            {
                //UnityEngine.Debug.LogError($"No view registered for {entity.GetType().Name}");
                //pk une error hein on est pas obligé de spawn un view pour chaque entity, on peut juste pas en avoir besoin
                return;
            }

            EntityView entityView = Instantiate(entityViewPrefab, entity.Position.ToVector2(), ViewMath.RadRotationToQuaternion(entity.Rotation));
            entityView.Initialize(entity);
        }

        public void StartGame()
        {
            if (InputProviders[0] == null || InputProviders[1] == null)
            {
                Debug.LogError("Tried to start game without two input providers given");
                return;
            }

            inputTakingMenu.SetActive(false);
            replay = null;
            replayLeft = null;
            GameRules.Set(LoadRules()); // also undoes the rules of a replay
            GameplayManager.Reset(InputProviders[0], InputProviders[1]);
        }

        /// <summary>
        /// Plays a match recorded on the Headless (MatchReplay): same map, same rules, same actions, so the same match
        /// frame for frame. Can be called from any screen; at the end, the end screen then the lobby, as after a match.
        /// </summary>
        public void StartReplay(MatchReplay matchReplay)
        {
            StopAllCoroutines(); // a menu transition in progress would show its menu over the replay
            AbortEndOfGameSequence();
            playButton.gameObject.SetActive(false);
            inputTakingMenu.SetActive(false);
            endCard.gameObject.SetActive(false);

            (replayLeft, _) = matchReplay.Start();
            replay = matchReplay;
            replayEndLogged = false;
            frameAccumulator = 0;
            LobbyState = LobbyStates.Input;
            UnityEngine.Debug.Log($"[Replay] {matchReplay.LeftName ?? "left"} (green) vs {matchReplay.RightName ?? "right"} (red), "
                                  + $"seed {matchReplay.Seed}, {matchReplay.Steps.Count} steps");
        }

        /// <summary>
        /// Ends at once an end-of-game sequence whose coroutine was just stopped (a replay started during the slow motion
        /// or the light explosion of the previous match): without it, the next match would be drawn slowed down, with
        /// views that no longer follow their entities.
        /// </summary>
        private void AbortEndOfGameSequence()
        {
            if (!isEndOfGameSequenceRunning)
                return;
            if (LightExplosion.instance != null)
                LightExplosion.instance.Cancel();
            if (SlowMotion.IsActive)
            {
                // As at the end of the sequence: the arena is cleared while the slow motion is on, so that every view goes
                // away silently when SlowMotion.End removes them (which also brings Time.timeScale back to 1)
                GameplayManager.ClearArena();
                SlowMotion.End();
            }
            Time.timeScale = 1f;
            isEndOfGameSequenceRunning = false;
        }

        private void Update()
        {
#if AUTOMATHON_DEBUG
            HandleDebugInput();
#endif

            int frames = FramesToSimulate();
            for (int i = 0; i < frames; i++)
            {
                // An AI agent that hasn't answered yet holds the match without freezing the game (it is asked again on
                // the next Unity frame): the agent decides on the state of its decision frame, as in training
                if (!GameplayManager.InputsReady())
                {
                    frameAccumulator = 0;
                    break;
                }

                GameplayManager.Update();
                EndReplayIfOver();
            }
        }

        private int FramesToSimulate()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            // Screens refresh at 60, 120, 144 Hz...: the simulation stays at GameplayConstants.FRAMERATE frames per second,
            // and drops the time it can't catch up with (tab in the background, slow machine)
            frameAccumulator += Time.unscaledDeltaTime * GameplayConstants.FRAMERATE;
            int frames = (int)frameAccumulator;
            frameAccumulator -= frames;
            return Mathf.Min(frames, MaxFramesPerUpdate);
#else
            return 1; // Application.targetFrameRate paces the game
#endif
        }

        /// <summary>
        /// A replay that stops before a tank dies (recording capped in time) ends with its last step. At the end of a
        /// replay, logs how the match ended and whether it is the end recorded on the Headless ("[Replay] End: ...").
        /// </summary>
        private void EndReplayIfOver()
        {
            if (replay == null || replayEndLogged)
                return;
            if (GameplayManager.State == GameplayManager.GameplayState.Game)
            {
                if (!replayLeft.IsFinished)
                    return;
                // Recording capped in time (4 200 frames, very rare with the shrinking zone): ends like a draw
                GameplayManager.StopWithoutWinner();
            }

            replayEndLogged = true;
            ReplayResult result = MatchReplay.CurrentResult();
            string verdict = replay.Result == null ? "no recorded end to compare"
                           : result.SameAs(replay.Result) ? "SAME as recorded"
                           : $"DIFFERENT from the recorded end ({replay.Result})";
            UnityEngine.Debug.Log($"[Replay] End: {result} -> {verdict}");
        }


#if AUTOMATHON_DEBUG
        // Dev-only solo play (gated by the AUTOMATHON_DEBUG scripting define symbol):
        //   F1  from the lobby  -> start a match driven entirely by keyboard + mouse
        //   Tab during a match  -> switch which tank the keyboard + mouse controls
        private void HandleDebugInput()
        {
            UnityEngine.InputSystem.Keyboard keyboard = UnityEngine.InputSystem.Keyboard.current;
            if (keyboard == null) return;

            if (GameplayManager.State == GameplayManager.GameplayState.Lobby)
            {
                // not during the end of game: its arena swap would wipe the new match
                if (keyboard.f1Key.wasPressedThisFrame && !isEndOfGameSequenceRunning)
                    StartDebugGame();
            }
            else if (keyboard.tabKey.wasPressedThisFrame) // GameplayState.Game
            {
                int next = 1 - Automathon.Game.Input.DebugInputProvider.ControlledIndex;
                Automathon.Game.Input.DebugInputProvider.ControlledIndex = next;
                UnityEngine.Debug.Log($"[DEBUG] Now controlling tank #{next} ({(next == 0 ? "Green" : "Red")})");
            }
        }

        private void StartDebugGame()
        {
            playButton.gameObject.SetActive(false);

            Automathon.Game.Input.DebugInputProvider.ControlledIndex = 0;
            InputProviders[0] = new Automathon.Game.Input.DebugInputProvider(0);
            InputProviders[1] = new Automathon.Game.Input.DebugInputProvider(1);

            StartGame(); // hides the input menu and calls GameplayManager.Reset with both providers

            UnityEngine.Debug.Log("[DEBUG] Solo match started. WASD/ZQSD move, mouse aim, LMB gun, " +
                "RMB missile, E shield, Left-Shift dash. Press Tab to switch tanks.");
        }
#endif

        private void OnEndGame(Tank.TeamType winner, FixedVector2 loserPosition)
        {
            LobbyState = LobbyStates.End;
            StartCoroutine(EndOfGameSequence(() => endCard.Scroll(winner), loserPosition.ToVector2()));
        }

        // A draw gets the same ending, with the light starting between the two ships
        private void OnDrawGame(FixedVector2 position)
        {
            LobbyState = LobbyStates.End;
            StartCoroutine(EndOfGameSequence(endCard.ScrollDraw, position.ToVector2()));
        }

        // Slow motion + light explosion. Under the light, once the screen is fully covered, the arena disappears and the
        // end menu takes its place; the light then fades away to reveal it, and it becomes usable once the light is gone.
        private IEnumerator EndOfGameSequence(System.Action showEndCard, Vector2 explosionWorldPosition)
        {
            isEndOfGameSequenceRunning = true;

            SlowMotion.Begin();
            UnityEngine.Coroutine slowingDown = StartCoroutine(RampTimeScale(slowMotionScale, slowMotionRampDuration));

            bool screenCovered = false, explosionFinished = false;
            // from the world position, so that the light stays on the wreck while the explosion shakes the camera
            LightExplosion.instance.LaunchLightExplosionAt(explosionWorldPosition,
                onScreenCovered: () => screenCovered = true,
                onFinished: () => explosionFinished = true);

            // WaitUntil is checked every frame, whatever Time.timeScale is
            yield return new WaitUntil(() => screenCovered);

            // ClearArena while the slow motion is still on, so that every view goes away silently (no missile explosion,
            // no sound) when SlowMotion.End removes them, which also brings Time.timeScale back to 1
            StopCoroutine(slowingDown);
            GameplayManager.ClearArena();
            SlowMotion.End();
            showEndCard();
            endCard.SetInteractable(false);

            yield return new WaitUntil(() => explosionFinished);

            endCard.SetInteractable(true);
            isEndOfGameSequenceRunning = false;
        }

        private static IEnumerator RampTimeScale(float target, float duration)
        {
            float start = Time.timeScale;
            for (float elapsed = 0f; elapsed < duration; elapsed += Time.unscaledDeltaTime)
            {
                float k = elapsed / duration;
                Time.timeScale = Mathf.Lerp(start, target, 1f - (1f - k) * (1f - k)); // ease-out: brakes hard at first, like a bullet time
                yield return null;
            }
            Time.timeScale = target;
        }

        /// <summary>
        /// Rules of the next match: the JSON file named by the AUTOMATHON_RULES environment variable, otherwise
        /// rules.json next to the game (next to the .exe or the .x86_64, next to the .app on macOS), with the same
        /// fields as GameRules (missing fields keep their default value). No file: default rules. Read at the start of
        /// every match, so the file can be edited between two matches.
        /// </summary>
        private static GameRules LoadRules()
        {
            if (Application.platform == RuntimePlatform.WebGLPlayer)
                return null;
            string path = Environment.GetEnvironmentVariable("AUTOMATHON_RULES");
            if (string.IsNullOrEmpty(path))
            {
                string dir = Path.GetDirectoryName(Application.dataPath);
                if (Application.platform == RuntimePlatform.OSXPlayer)
                    dir = Path.GetDirectoryName(dir);  // dataPath = <...>/AutomathonGame.app/Contents
                path = Path.Combine(dir, "rules.json");
            }
            if (!File.Exists(path))
                return null;
            try
            {
                GameRules rules = JsonConvert.DeserializeObject<GameRules>(File.ReadAllText(path));
                UnityEngine.Debug.Log($"Match rules read from {path}: {JsonConvert.SerializeObject(rules)}");
                return rules;
            }
            catch (Exception e)
            {
                UnityEngine.Debug.LogError($"Could not read the match rules in {path}, default rules used: {e.Message}");
                return null;
            }
        }

        public void OnEndScreenDone()
        {
            replay = null;
            replayLeft = null;
            LobbyState = LobbyStates.Input;
            inputTaking.ResetInputTaking(resetInput: false);
            inputTakingMenu.SetActive(true);

        }

        private void DebugForward(string message)
            => UnityEngine.Debug.Log(message);

        private void DebugErrorForward(string message)
            => UnityEngine.Debug.LogError(message);

        private void OnEnable()
        {
            if (!subbedToSpawnEntityView)
            {
                GameplayManager.EntitySpawned += SpawnEntityViewFromDict;
                GameplayManager.GameEnded += OnEndGame;
                GameplayManager.GameDrawn += OnDrawGame;
                subbedToSpawnEntityView = true;
            }
        }

        private void OnDisable()
        {
            // the end-of-game coroutine stops with this component: don't leave the game slowed down
            SlowMotion.End();
            isEndOfGameSequenceRunning = false;

            if (subbedToSpawnEntityView)
            {
                GameplayManager.EntitySpawned -= SpawnEntityViewFromDict;
                GameplayManager.GameEnded -= OnEndGame;
                GameplayManager.GameDrawn -= OnDrawGame;
                subbedToSpawnEntityView = false;
            }
        }

        private void OnDestroy()
        {
            //ServerHandler.StopServer();
            GameplayManager.Dispose();

            Debug.LogEvent -= DebugForward;
            Debug.LogErrorEvent -= DebugErrorForward;
            playButton.onClick.RemoveAllListeners();
        }
    }
}