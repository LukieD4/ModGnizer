# Gnizer

A Minecraft Mod Organizer

## TL;DR

Gnizer is a CLI-based tool that lets you manage, share, and sync
modpacks, mods, and worlds across Modrinth and CurseForge ecosystems.
Simple. Fast. No bloat.
  
The auto update feature is tempermental.  
**Please use version v434 or higher.**  

------------------------------------------------------------------------

## What It Does

Gnizer acts as a bridge between different Minecraft mod manager
platforms.  
(Inspiration taken from the CurseForge share code)

-   Import mods from Modrinth or CurseForge
-   Export mods in a shareable format
-   Sync mods between computers (manually)
-   Share entire worlds with required mod dependencies
-   Validate installed mods against a profile
-   Lightweight and easy to use, a menu based app.

------------------------------------------------------------------------

## How It Works

### 1. Profile Detection

Gnizer scans your local Modrinth or CurseForge instances and integrates menu based operations while pushing live data to the command line interface. i.e. your profiles.

### 2. Installation Detection  

It searches through your system's registry keys to find any instance of Modrinth or CurseForge + CurseForge (Overwolf)  

It additionally searches for instances of 7zip and WinRAR, if you want **PASSWORD PROTECTED** archives, please install one of them, I have no affilation with tmpfiles.org so it's your responsibility if your upload is accessed.  


### 3. Packaging

Gnizer expects the full copied text when the user wants to load data.  
The text is a working and friendly way towards a manifest.


------------------------------------------------------------------------

## Why Gnizer?

-   An all in one solution
-   No faff with having to swap around with mod managers
-   Generates reports on modpack instances (what will be replaced and what files are identical)

------------------------------------------------------------------------
