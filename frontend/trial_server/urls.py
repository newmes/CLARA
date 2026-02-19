"""URL Configuration for Clinical Trial Viewer."""
from django.urls import path
from viewer import views

urlpatterns = [
    # Landing
    path('', views.landing, name='landing'),

    # Trial viewer
    path('trial/<str:run_id>/', views.trial_viewer, name='trial_viewer'),
    path('trial/<str:run_id>/<int:day>/', views.trial_viewer, name='trial_viewer_day'),

    # Patient state
    path('patient/<str:run_id>/<str:patient_id>/',
         views.patient_state, name='patient_state'),
    path('patient/<str:run_id>/<str:patient_id>/<int:day>/',
         views.patient_state, name='patient_state_day'),

    # API — day data
    path('api/day/<str:run_id>/<int:day>/',
         views.api_day_data, name='api_day_data'),

    # API — patient timeline
    path('api/patient/<str:run_id>/<str:patient_id>/',
         views.api_patient_timeline, name='api_patient_timeline'),

    # API — run meta
    path('api/run/<str:run_id>/',
         views.api_run_meta, name='api_run_meta'),

    # SSE stream
    path('api/sse/<str:run_id>/',
         views.sse_stream, name='sse_stream'),

    # Map metadata
    path('api/map/<str:run_id>/',
         views.api_map_meta, name='api_map_meta'),

    # Game — landing & play
    path('game/<str:run_id>/',
         views.game_landing, name='game_landing'),

    # Game — play
    path('game/<str:run_id>/<str:patient_id>/',
         views.game_play, name='game_play'),

    # Game API
    path('api/game/start', views.api_game_start, name='api_game_start'),
    path('api/game/advance', views.api_game_advance, name='api_game_advance'),
    path('api/game/greet', views.api_game_greet, name='api_game_greet'),
    path('api/game/chat', views.api_game_chat, name='api_game_chat'),
    path('api/game/end-chat', views.api_game_end_chat, name='api_game_end_chat'),
    path('api/game/skip', views.api_game_skip, name='api_game_skip'),
    path('api/game/reveal/<str:session_id>/',
         views.api_game_reveal, name='api_game_reveal'),
    path('api/game/debrief', views.api_game_debrief, name='api_game_debrief'),
    path('api/game/copilot', views.api_game_copilot, name='api_game_copilot'),
    path('api/game/sessions', views.api_game_sessions, name='api_game_sessions'),

    # Compare dashboard
    path('compare/<str:run_id>/',
         views.compare_dashboard, name='compare_dashboard'),
    path('api/compare/<str:run_id>/',
         views.api_compare_data, name='api_compare_data'),
    path('api/compare/<str:run_id>/regenerate/',
         views.api_compare_regenerate, name='api_compare_regenerate'),

    # Live simulation API
    path('api/sim/start', views.api_sim_start, name='api_sim_start'),
    path('api/sim/status/<str:run_id>/',
         views.api_sim_status, name='api_sim_status'),
    path('api/sim/list', views.api_sim_list, name='api_sim_list'),
    path('api/sim/stop/<str:run_id>/',
         views.api_sim_stop, name='api_sim_stop'),
    path('api/sim/log/<str:run_id>/',
         views.api_sim_log, name='api_sim_log'),

    # Doc Agent — SAE document generation
    path('api/doc/generate', views.api_doc_generate, name='api_doc_generate'),
    path('api/doc/saes/<str:run_id>/<str:patient_id>/',
         views.api_doc_list_saes, name='api_doc_list_saes'),
    path('api/doc/download/<str:run_id>/<str:patient_id>/<str:filename>',
         views.api_doc_download, name='api_doc_download'),
    path('api/doc/list/<str:run_id>/',
         views.api_doc_list, name='api_doc_list'),
    path('api/doc/save', views.api_doc_save, name='api_doc_save'),

    # Doc Agent — Documents Hub & SAE Report Editor
    path('doc/<str:run_id>/',
         views.doc_hub, name='doc_hub'),
    path('doc/<str:run_id>/<str:patient_id>/<str:ae_slug>/',
         views.sae_report_editor, name='sae_report_editor'),
]