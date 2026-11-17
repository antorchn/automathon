using System;
using System.Collections.Generic;
using UnityEngine;

public class SoundManager : MonoBehaviour
{
    public static SoundManager instance;
    private AudioSource[] sources;
    private AudioLowPassFilter[] muffleFilters;

    [SerializeField] private int bufferSize = 10;
    [SerializeField] private int index = 0;

    [System.Serializable]
    public class Sound
    {
        public string name;
        public AudioClip clip;
        [Range(0f, 1f)]
        public float volume = 1f;
    }

    [SerializeField] private Sound[] sounds;

    private Dictionary<string, Sound> soundDict;

    private float muffleCutoff = 22000f;

    /// <summary>Low-pass cutoff (Hz) shared by every sound played with muffled = true. 22000 = not muffled.</summary>
    public float MuffleCutoff
    {
        get => muffleCutoff;
        set
        {
            muffleCutoff = Mathf.Clamp(value, 10f, 22000f);
            foreach (AudioLowPassFilter filter in muffleFilters)
                filter.cutoffFrequency = muffleCutoff; //only the enabled ones (muffled sounds) filter anything
        }
    }


    void Awake()
    {
        if (instance == null)
        {
            instance = this;
        }

        sources = new AudioSource[bufferSize];
        muffleFilters = new AudioLowPassFilter[bufferSize];

        for (int i = 0; i < bufferSize; i++)
        {
            //one child object per source: an audio filter applies to the source of its own GameObject
            GameObject sourceObject = new GameObject("Source " + i);
            sourceObject.transform.SetParent(transform, false);
            sources[i] = sourceObject.AddComponent<AudioSource>();
            sources[i].playOnAwake = false;
            muffleFilters[i] = sourceObject.AddComponent<AudioLowPassFilter>();
            muffleFilters[i].enabled = false;
        }

        soundDict = new Dictionary<string, Sound>();

        foreach (Sound s in sounds)
        {
            soundDict[s.name] = s;
        }
    }

    public int PlaySound(string name, bool playOneShot = true, float startingVolume = -1f)
    {
        if (!soundDict.TryGetValue(name, out Sound sound))
        {
            Debug.LogError("Sound : '" + name + "' was not found.");
            return -1;
        }

        return PlayClip(sound.clip, startingVolume < 0f ? sound.volume : startingVolume, playOneShot);
    }

    /// <summary>Plays a clip that isn't in the list: generated in code, or picked in its owner's own settings.</summary>
    /// <param name="playOneShot">One-shots can overlap on a source. Otherwise the clip replaces the source's sound, and
    /// the returned index drives it (SetVolume, SetPitch, StopSound).</param>
    /// <param name="pitch">1 = normal, below 1 = deeper and longer.</param>
    /// <param name="loop">Plays until StopSound (never as a one-shot). Meanwhile, no other sound takes its source over.</param>
    /// <param name="muffled">Through a low-pass filter, see MuffleCutoff.</param>
    /// <returns>The index of the source playing it.</returns>
    public int PlayClip(AudioClip clip, float volume = 1f, bool playOneShot = true, float pitch = 1f, bool loop = false, bool muffled = false)
    {
        int i = NextSource();
        AudioSource source = sources[i];

        //a source keeps its settings from one sound to the next: they are all set again
        source.pitch = pitch;
        source.loop = loop;
        muffleFilters[i].enabled = muffled;

        if (playOneShot && !loop)
        {
            source.volume = 1f;
            source.PlayOneShot(clip, volume);
        }
        else
        {
            source.clip = clip;
            source.volume = volume;
            source.Play();
        }

        return i;
    }

    //Round robin over the sources, skipping the ones held by a looping sound
    private int NextSource()
    {
        for (int tries = 0; tries < sources.Length; tries++)
        {
            int i = index;
            index = (index + 1) % sources.Length;
            if (!(sources[i].loop && sources[i].isPlaying))
                return i;
        }

        Debug.LogWarning("[SoundManager] Every source is held by a looping sound: raise bufferSize.");
        int taken = index;
        index = (index + 1) % sources.Length;
        return taken;
    }

    public void StopSound(int i)
    {
        if (IsValid(i))
            sources[i].Stop();
    }

    public void SetVolume(int i, float volume)
    {
        if (IsValid(i))
            sources[i].volume = volume;
    }

    public void SetPitch(int i, float pitch)
    {
        if (IsValid(i))
            sources[i].pitch = pitch;
    }

    //-1 is what PlaySound returns for an unknown sound
    private bool IsValid(int i) => i >= 0 && i < sources.Length;
}
