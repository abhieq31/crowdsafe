# Product audit: working backwards from the customer

*An audit of the web app in the style Jeff Bezos describes in his shareholder letters: start with the
customer and work backwards, obsess over friction, write the press release first, and decide with
mechanisms rather than good intentions. This is our reading of those principles, not Amazon's.*

## 1. Who is the customer?

**A district officer, police inspector or event organiser, two weeks before a mela, a match or a puja.**
They are on a phone or an office laptop, they are not crowd-safety engineers, and they carry the
responsibility if something goes wrong. What they want is not a simulator. They want to know:
**"Is my event safe, and what exactly do I have to do?"**

## 2. The press release we are working backwards from

> **Room to Breathe tells any organiser in 30 seconds whether their event is safe.**
> Type the name of the ground. Room to Breathe finds it on the map, outlines it, and finds its gates.
> Say how many people are coming. You get a verdict (Safe, Possible with precautions, or Not safe) with
> the reasons and a numbered list of what to change, then a deployment plan for police, traffic police
> and health teams around the venue. It is free, needs no sign-up, and nothing leaves your device.
>
> *"Before, a crowd plan took our office two days and a consultant. Now we check every permit
> application before we sign it."* (the customer we are building for)

## 3. FAQ

**What does the customer have to do?** Type a place, confirm the outline, type a number. Three actions.

**What if the outline or gates are wrong?** They can drag the corners, add or remove gates, or correct a
street. Every number says whether it was measured or estimated.

**Why should they trust it?** It shows its working: Green Guide capacity, Fruin levels of service and
OpenStreetMap data, with every assumption written down. It is open source. It is decision support, so a
trained person still decides.

## 4. What the audit found: friction, by severity

| # | Friction | Evidence | Decision |
|---|---|---|---|
| 1 | **No answer before effort.** The first screen is a world map. Value only comes after drawing polygons and gates, which most officers have never done. | Fresh visit: a world map and "Draw a zone" | **Find the venue for them.** Search by name, then outline the ground automatically from OpenStreetMap (stadium, park, ground, building) and detect gates where paths cross the boundary. One click: *Use this venue*. |
| 2 | **The decision is far from the action.** The people box, the *Check* button and the verdict are three cards down the page. | Scrolling needed on laptop and phone | **A decision box** that stays in view (on the right on desktop, at the bottom on phones): venue, people, arrival time, and one big button. The verdict appears in the same place. |
| 3 | **Paper cut: scrolling the page zooms the map.** The wheel over the map zoomed out to the whole world. | Reproduced on the live site | Map zoom by wheel only after the map is clicked; a hint says so. |
| 4 | **Jargon.** "Zones", "gates", "level of service E" and "usable %" mean little to an organiser. | Labels across step 1 and step 3 | Plain words first ("area where people stand", "way in or out"), with the expert term in *Why?* details. |
| 5 | **No trust signal at the moment of decision.** Free, private and open source are said only in the footer. | Footer | Trust chips under the main button: *Free · No sign-up · Nothing uploaded · Open source*. |
| 6 | **Results read like a report, not a decision.** The key numbers (safe capacity, time to clear) are inside sentences. | Verdict card | **Big numbers** in a scorecard: safe capacity, people expected, time to clear, warning time. Then the fixes as a checklist, each with a next action. |
| 7 | **Step 3 is a second, separate journey.** It repeats inputs and does not say when to use it. | Step 3 form | After a verdict, one obvious next button: *Plan police, traffic and medical teams →*, with sensible defaults already filled in. |
| 8 | **The look does not help the eye.** The generic dark theme with low-contrast buttons makes the next action hard to find, and there is no sense of progress. | Screenshots | **A calm identity of its own**: deep teal on soft paper, generous space, one teal action at a time, gentle tinted verdict colours, and a 4-step guide (venue → people → verdict → teams) that shows where you are. |

## 5. Tenets (to settle disagreements quickly)

1. **Customer time is the scarcest resource.** Every screen must reach a verdict in as few actions as possible.
2. **One main action per screen.** If two buttons look equally important, one of them is wrong.
3. **Show the working, hide the jargon.** Plain words up front, the expert detail one click away.
4. **Never surprise.** Estimates are labelled as estimates, and nothing is uploaded.
5. **Day 1.** The plan is a starting point to check on the ground, never the last word.

## 6. Measures (mechanisms, not intentions)

| Measure | Before | Target | How it is checked |
|---|---|---|---|
| Actions from landing to first verdict | about 12 (search, draw a zone with 4+ taps, draw gates, type a number, check) | **3** (search, *Use this venue*, check) | Browser test in the PR |
| Time to first verdict on a laptop | more than 2 min for a new user | **under 30 s** | Browser test, measured |
| Main actions visible without scrolling | 0 of 2 | **2 of 2** (search, check) | Screenshot at 1280×800 and 375×812 |
| Page scroll hijacked by the map | yes | **no** | Wheel test over the map |

The engine and its tests are unchanged: the redesign must not change a single verdict.
