-- Require authenticated users for attendance actions and views

revoke execute on function public.mark_attendance(text, text) from anon;
revoke select on public.v_today_attendance from anon;

grant execute on function public.mark_attendance(text, text) to authenticated;
grant select on public.v_today_attendance to authenticated;
