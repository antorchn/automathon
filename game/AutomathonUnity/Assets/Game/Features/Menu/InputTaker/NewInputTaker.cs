using Automathon.AI;
using Automathon.Game.Input;
using Automathon.Game.Web;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Automathon.Game.View
{
    public class NewInputTaker : MonoBehaviour
    {
        public InputProvider InputProvider;
        private Image currentDeviceImg;

        [SerializeField] private int playerIndex;
        [SerializeField] private InputManager inputManager;

        [Header("Device Images")]
        [SerializeField] private GameObject noInputGameObject;
        private bool noInputActive;
        [SerializeField] private Image xboxControllerDeviceImg;
        [SerializeField] private Image playStationControllerDeviceImg;
        [SerializeField] private Image switchControllerDeviceImg;
        [SerializeField] private Image leftKeyboardDeviceImg;
        [SerializeField] private Image rightKeyboardDeviceImg;
        [SerializeField] private Image aiDeviceImg;

        private Dictionary<PlayerInputProvider.PlayerControlsType, Image> controlsToImg;

        [Header("Indication Frame")]
        [SerializeField] private TMP_Text indicationText;

        [Header("AI Input")]
        [SerializeField] private TMP_InputField inputField;
        [SerializeField] private TMP_Text tcpText;

        [Header("Misc")]
        [SerializeField] private TMP_Text playerOrderAndFeedbackText;

        [SerializeField] private GameObject tankImageGO;

        private bool waitingForInput;

        [SerializeField] private Button changePlayerAIButton;
        private bool isPlayer;
        [SerializeField] private float connectionSuccessDisplayTime = 1f;

        [SerializeField] private StartButton startButton;

        [SerializeField] private InputTaking inputTaking;

        private void Awake()
        {
            controlsToImg = new Dictionary<PlayerInputProvider.PlayerControlsType, Image>()
            {
                { PlayerInputProvider.PlayerControlsType.Xbox, xboxControllerDeviceImg },
                { PlayerInputProvider.PlayerControlsType.PlayStation, playStationControllerDeviceImg },
                { PlayerInputProvider.PlayerControlsType.Switch, switchControllerDeviceImg },
                { PlayerInputProvider.PlayerControlsType.LeftKeyboard, leftKeyboardDeviceImg },
                { PlayerInputProvider.PlayerControlsType.RightKeyboard, rightKeyboardDeviceImg },
            };

            inputField.onEndEdit.AddListener(FinishedTyping);
            inputField.onSelect.AddListener(OnSelect);

            if (tcpText != null)
                tcpText.text = AgentAddress.FieldLabel;
            if (AgentAddress.IsBrowser && inputField.placeholder is TMP_Text placeholder)
                placeholder.text = "agent name"; // instead of a port number

            SwitchToPlayer();

            changePlayerAIButton.onClick.AddListener(OnPlayerAIChange);
        }

        public void Reset()
        {
            ResetInput();
            SwitchToPlayer();
        }

        private void OnPlayerAIChange()
        {
            SoundManager.instance.PlaySound("MenuButton");
            HideTankImage();
            ResetInput();
            if (isPlayer)
            {
                SwitchToAI();
            }
            else
            {
                SwitchToPlayer();
            }
        }

        private Coroutine connecting;
        private AIInputProvider connectingProvider;

        private void FinishedTyping(string finalText)
        {
            ChangeIndicationTextOnFinishedTyping();

            AIInputProvider aIInputProvider;
            try
            {
                aIInputProvider = AgentAddress.CreateProvider(finalText);
            }
            catch (System.Exception e)
            {
                UnityEngine.Debug.LogWarning($"[AI] {e.Message}");
                FailedAIConnection_Address();
                return;
            }

            CancelConnection();
            connectingProvider = aIInputProvider;
            connecting = StartCoroutine(PingThenConnect(aIInputProvider));
        }

        /// <summary>Waits for the agent's "Pong" without freezing the game (the browser can't wait on the main thread).</summary>
        private System.Collections.IEnumerator PingThenConnect(AIInputProvider aIInputProvider)
        {
            playerOrderAndFeedbackText.text = "CONNECTING...";
            aIInputProvider.StartPing();
            bool? answered;
            while ((answered = aIInputProvider.PollPing(aIInputProvider.ResponseTimeout)) == null)
                yield return null;
            connecting = null;
            connectingProvider = null;

            if (answered == true)
            {
                SuccessfulAIConnection(aIInputProvider);
            }
            else
            {
                aIInputProvider.EndLife(); // release the connection, a new provider is created on the next try
                FailedAIConnection_Ping();
            }
        }

        private void SuccessfulAIConnection(AIInputProvider aIInputProvider)
        {
            SoundManager.instance.PlaySound("SuccessfullConnection");
            SetInputProvider(aIInputProvider);
            SetCurrentDeviceImg(aiDeviceImg);
            HideTCPInput();
            StartCoroutine(ShowSuccessThenTank());
        }

        private void FailedAIConnection_Ping()
        {
            SoundManager.instance.PlaySound("FailedConnection");
            playerOrderAndFeedbackText.text = AgentAddress.IsBrowser
                ? "COULD NOT CONNECT TO THIS AGENT. CHECK ITS NAME AND THAT THE SERVER IS RUNNING."
                : "COULD NOT CONNECT TO AI SERVER. MAKE SURE PYTHON'S PLAY SCRIPT IS ALREADY RUNNING.";
        }

        private void FailedAIConnection_Address()
        {
            SoundManager.instance.PlaySound("FailedConnection");
            playerOrderAndFeedbackText.text = AgentAddress.IsBrowser ? "INVALID AGENT NAME" : "INVALID TCP ADDRESS";
        }

        private void HideTCPInput()
        {
            tcpText.gameObject.SetActive(false);
            inputField.gameObject.SetActive(false);
        }
        private void ShowTCPInput()
        {
            tcpText.gameObject.SetActive(true);
            inputField.gameObject.SetActive(true);
        }

        private void OnSelect(string text)
        {
            ResetInput();
            ChangeIndicationTextOnSelect();
        }
        private void ChangeIndicationTextOnSelect()
        {
            playerOrderAndFeedbackText.text = AgentAddress.FieldHint;
        }

        private void ChangeIndicationTextOnFinishedTyping()
        {
            playerOrderAndFeedbackText.text = "";
        }

        private void Update()
        {
            if (waitingForInput && inputManager.TryFindNewInput(out PlayerInputProvider playerInputProvider) && inputTaking.isMenuActive)
            {
                SuccessfulPlayerConnection(playerInputProvider);
            }
        }

        private void SuccessfulPlayerConnection(PlayerInputProvider playerInputProvider)
        {
            SoundManager.instance.PlaySound("SuccessfullConnection");
            SetInputProvider(playerInputProvider);
            Image img = controlsToImg[playerInputProvider.ControlsType];
            SetCurrentDeviceImg(img);
            waitingForInput = false;
            StartCoroutine(ShowSuccessThenTank());
        }

        private System.Collections.IEnumerator ShowSuccessThenTank()
        {
            playerOrderAndFeedbackText.text = "CONNECTION SUCCESSFUL !";
            yield return new WaitForSeconds(connectionSuccessDisplayTime);
            playerOrderAndFeedbackText.text = "";
            ShowTankImage();

        }

        /// <summary>Abandons a connection whose ping is still waiting (the player typed again or changed their mind).</summary>
        private void CancelConnection()
        {
            if (connecting != null)
                StopCoroutine(connecting);
            connectingProvider?.EndLife();
            connecting = null;
            connectingProvider = null;
        }

        private void ResetInput()
        {
            if (connecting != null)
                playerOrderAndFeedbackText.text = "";
            CancelConnection();
            if (InputProvider != null)
            {
                WorldView.Instance.InputProviders[playerIndex] = null;
                if (InputProvider is AIInputProvider aIInputProvider)
                    aIInputProvider.EndLife();
                InputProvider = null;
            }
        }

        private void SetInputProvider(InputProvider inputProvider)
        {
            InputProvider = inputProvider;
            WorldView.Instance.InputProviders[playerIndex] = inputProvider;
        }



        private void SetCurrentDeviceImg(Image img)
        {
            noInputGameObject.SetActive(false);
            if (currentDeviceImg)
                currentDeviceImg.gameObject.SetActive(false);
            if (img == null)
                return;
            currentDeviceImg = img;
            currentDeviceImg.gameObject.SetActive(true);
        }

        private void SetNoInput()
        {
            if (currentDeviceImg)
                currentDeviceImg.gameObject.SetActive(false);
            noInputGameObject.SetActive(true);
        }

        private void ShowTankImage()
        {
            tankImageGO.SetActive(true);
        }

        private void HideTankImage()
        {
            tankImageGO.SetActive(false);
        }

        private void OnDestroy()
        {
            inputField.onEndEdit.RemoveAllListeners();
            inputField.onSelect.RemoveAllListeners();
            changePlayerAIButton.onClick.RemoveAllListeners();
        }

        private void SwitchToAI()
        {
            HideTankImage();
            isPlayer = false;
            waitingForInput = false;
            indicationText.text = "AI";
            ChangeIndicationTextOnSelect();
            SetCurrentDeviceImg(null);
            inputField.text = "";
            ShowTCPInput();
        }

        private void SwitchToPlayer()
        {
            HideTankImage();
            isPlayer = true;
            waitingForInput = true;
            indicationText.text = "PLAYER";
            playerOrderAndFeedbackText.text = "PRESS ANY INPUT...";
            SetNoInput();
            HideTCPInput();
        }
    }

}