
# Human review results
- naprooms need the same amount of lockers as beds
- naprooms needs as much beds as we can pack it (as long they are reachable)
- naprooms beds need to be organized (as long they are reachable
- storage rooms needs to be filled to the brim
- in general, less huddle, focus or meeting rooms, more office space
- server rooms (in corp office at least) are mostly empty
- server rooms (in office at least) should have a small chance for mantraps (the higher security, the higher the chance if possible)
- mantraps are too large
- the security gates in mantraps are not aligned with room/doors/pathway
- the security gates at the reception are not aligned with the door/pathway
- always generate a roof if there is more one floor above (which does not count as floor above)
- a higher chance of safe rooms for executive offices (i want to see at least one in this floor as average. but can be set as min if that's easier)
- office (at least): floor 2 and floor 3 look exactly the same (except for the doors, and some objects in the corridor). Maybe seed every floor a bit different?
- reception: the guard desk shouldn't be in front of the reception desk, but rather perpendicular looking at the entrance/security gates
- There should be at least one executive office in the office (with a small chance to have a safe room)




# NOT YET:
- general: we need an identifier with which version created which image
- label/differentiate the tests: those who test functionality, and those who are hard violations


# Done (branch human-review)
Nap rooms: lockers match pods (`match:`), pods packed in rows then along the walls; storage and server rooms
filled; fewer huddle/focus/meeting rooms (fill weights); mantraps: smaller, corner vestibule for office server
rooms by security level; gates (`gates` placement) flank the entrance lane, guard post (`guard`) beside them;
roof is an extra level over the top floor; executive offices (and safe rooms) on the top floor; every floor
gets its own partition.
