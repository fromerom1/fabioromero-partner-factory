-- The pipeline runs in UTC sessions (set on the service user by the platform bootstrap).
-- Fails if this session is offset from UTC. Casting the current time to a zone-less timestamp
-- uses the session time zone, so the two values below only agree when the offset is zero.

select session_offset_seconds
from (
    select datediff(
        second,
        convert_timezone('UTC', current_timestamp())::timestamp_ntz,
        current_timestamp()::timestamp_ntz
    ) as session_offset_seconds
)
where session_offset_seconds <> 0
