# Second Unit — Scene Breakdown

## 1. Current flow

1. Breakdown stays locked until the Screenplay is approved.
2. Gemini reads every approved scene and extracts practical production requirements.
3. Requirements are grouped beneath their source scene and categorized as cast,
   location, props, wardrobe, makeup, vehicles, animals, sound, effects, equipment,
   crew, time of day, or risk/logistics.
4. The filmmaker can add, edit, or remove requirements.
5. **Regenerate with AI** can atomically replace the active suggestions while
   retaining the previous version in history.
6. Explicit approval freezes the reviewed Breakdown for scheduling.
7. For Location requirements, the filmmaker can choose a production base and
   maximum driving time, then search Google Maps and shortlist suitable places.

## 2. Safety and authority

- Every generated requirement must cite a real screenplay scene ID.
- Every scene must receive at least one requirement.
- Provider output is validated before one atomic SQLite write.
- The model cannot approve a Screenplay or Breakdown.
- Every mutation is revision checked and versioned.
- Agent attempts and provider failures are persisted for diagnosis.

## 3. Current data

SQLite stores production breakdowns, individual requirements, immutable version
snapshots, and agent-run diagnostics. Archived requirements remain recoverable in
history even though the active UI no longer displays them.

Location searches persist only Google Place IDs, the travel limit, and shortlist
state. Google place details and route measurements remain transient.

## 4. Next stage

The approved Breakdown will feed the Shooting Schedule. Scheduling is not built yet.
