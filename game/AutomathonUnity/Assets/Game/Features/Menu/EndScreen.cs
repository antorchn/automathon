using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Automathon.Game
{
    public class EndScreen : MonoBehaviour
    {
        [SerializeField] private TMP_Text winnerText;
        [SerializeField] private Button endOfEndScreenButton;

        [Header("Teams")]
        [Tooltip("The accents of this screen's ship sprites: lime on the left (green team), orange on the right (red team)")]
        [SerializeField] private Color greenTeamColor = new Color(0.8f, 1f, 0f);
        [SerializeField] private Color redTeamColor = new Color(1f, 0.4f, 0f);
        [Tooltip("Named after the colors the players actually see")]
        [SerializeField] private string greenTeamName = "LIME";
        [SerializeField] private string redTeamName = "ORANGE";

        [Header("Winner and loser ships")]
        [Tooltip("Found by name (LeftSpaceship) when empty")]
        [SerializeField] private Image greenTeamShip;
        [Tooltip("Found by name (RightSpaceship) when empty")]
        [SerializeField] private Image redTeamShip;
        [SerializeField] private float winnerScale = 1.2f;
        [SerializeField] private float loserScale = 0.8f;
        [Tooltip("Multiplies the loser ship's colors: darker and a bit transparent")]
        [SerializeField] private Color loserTint = new Color(0.3f, 0.3f, 0.3f, 0.6f);
        [Tooltip("Slow breathing of the winner ship: relative amplitude, and cycles per second")]
        [SerializeField] private float winnerPulseAmplitude = 0.04f;
        [SerializeField] private float winnerPulseFrequency = 0.7f;

        private Ship greenShip, redShip, winnerShip;

        //A ship image as authored, so that every end of game starts again from it
        private sealed class Ship
        {
            private readonly Image image;
            private readonly RectTransform rect;
            private readonly Vector3 baseScale;
            private readonly Vector2 basePosition;
            private readonly Color baseColor;

            public Ship(Image image)
            {
                this.image = image;
                rect = image.rectTransform;
                baseScale = rect.localScale;
                basePosition = rect.anchoredPosition;
                baseColor = image.color;
            }

            //Around the ship's center: its pivot is on its outer edge (it is anchored to the side of the screen), so a
            //plain scale would make it grow or shrink toward that edge. Moving it by what the center would have moved
            //keeps the center in place.
            public void SetScale(float scale)
            {
                rect.localScale = baseScale * scale;
                Vector2 pivotToCenter = Vector2.Scale(new Vector2(0.5f, 0.5f) - rect.pivot, rect.rect.size);
                rect.anchoredPosition = basePosition + Vector2.Scale(pivotToCenter, baseScale) * (1f - scale);
            }

            public void SetTint(Color tint) => image.color = baseColor * tint;
        }

        private void Awake()
        {
            endOfEndScreenButton.onClick.AddListener(Done);

            Image greenImage = greenTeamShip != null ? greenTeamShip : FindImage("LeftSpaceship");
            Image redImage = redTeamShip != null ? redTeamShip : FindImage("RightSpaceship");
            greenShip = greenImage != null ? new Ship(greenImage) : null;
            redShip = redImage != null ? new Ship(redImage) : null;
        }

        private Image FindImage(string objectName)
        {
            foreach (Image image in GetComponentsInChildren<Image>(true))
            {
                if (image.name == objectName)
                    return image;
            }

            UnityEngine.Debug.LogWarning($"[EndScreen] No '{objectName}' image found: assign the team ships by hand.", this);
            return null;
        }

        public void Scroll(Tank.TeamType winner)
        {
            //Reset everything

            gameObject.SetActive(true);

            bool greenWon = winner == Tank.TeamType.Green;
            winnerText.color = greenWon ? greenTeamColor : redTeamColor;
            winnerText.text = $"{(greenWon ? greenTeamName : redTeamName)} WINS !";

            //the winner's ship stands out, the loser's is dimmed: who won reads at a glance, even without the text
            Ship loserShip = greenWon ? redShip : greenShip;
            winnerShip = greenWon ? greenShip : redShip;
            winnerShip?.SetScale(winnerScale);
            winnerShip?.SetTint(Color.white);
            loserShip?.SetScale(loserScale);
            loserShip?.SetTint(loserTint);

            //Make the object scroll with animations
        }

        /// <summary>Draw: both tanks destroyed in the same frame, or equal health at the time limit.</summary>
        public void ScrollDraw()
        {
            gameObject.SetActive(true);
            winnerText.color = Color.white;
            winnerText.text = "DRAW !";

            //neither ship stands out
            winnerShip = null;
            greenShip?.SetScale(1f);
            greenShip?.SetTint(Color.white);
            redShip?.SetScale(1f);
            redShip?.SetTint(Color.white);
        }

        private void Update()
        {
            float pulse = 1f + winnerPulseAmplitude * Mathf.Sin(2f * Mathf.PI * winnerPulseFrequency * Time.unscaledTime);
            winnerShip?.SetScale(winnerScale * pulse);
        }

        /// <summary>The menu appears under the light of the explosion, and can only be used once the light is gone.</summary>
        public void SetInteractable(bool interactable) => endOfEndScreenButton.interactable = interactable;

        private void Done()
        {
            //Scroll back up
            gameObject.SetActive(false);
            WorldView.Instance.OnEndScreenDone();
        }

        private void OnDestroy()
        {
            endOfEndScreenButton.onClick.RemoveAllListeners();
        }
    }
}
