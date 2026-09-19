"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type Place = { name: string; country?: string; latitude: number; longitude: number; timezone?: string };
type Weather = { current: { temperature_2m: number; apparent_temperature: number; relative_humidity_2m: number; wind_speed_10m: number; weather_code: number; is_day: number }; hourly: { time: string[]; temperature_2m: number[]; precipitation_probability: number[]; weather_code: number[] }; daily: { time: string[]; weather_code: number[]; temperature_2m_max: number[]; temperature_2m_min: number[]; precipitation_probability_max: number[] }; current_units: { temperature_2m: string; wind_speed_10m: string } };

const descriptions: Record<number, string> = { 0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Foggy", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers", 81: "Rain showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm" };
const icons: Record<number, string> = { 0: "☀", 1: "◐", 2: "⛅", 3: "☁", 45: "≋", 48: "≋", 51: "☂", 53: "☂", 55: "☂", 61: "☂", 63: "☂", 65: "☂", 71: "❄", 73: "❄", 75: "❄", 80: "☂", 81: "☂", 82: "☂", 95: "ϟ", 96: "ϟ", 99: "ϟ" };
const text = (code: number) => descriptions[code] ?? "Unknown conditions";
const glyph = (code: number) => icons[code] ?? "•";

export default function HomePage() {
  const [place, setPlace] = useState<Place>({ name: "London", country: "United Kingdom", latitude: 51.5074, longitude: -0.1278, timezone: "Europe/London" });
  const [weather, setWeather] = useState<Weather | null>(null);
  const [query, setQuery] = useState("");
  const [unit, setUnit] = useState<"celsius" | "fahrenheit">("celsius");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setMessage("");
    try {
      const params = new URLSearchParams({ latitude: String(place.latitude), longitude: String(place.longitude), current: "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day", hourly: "temperature_2m,precipitation_probability,weather_code", daily: "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max", forecast_days: "7", timezone: "auto", temperature_unit: unit, wind_speed_unit: "kmh" });
      const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
      if (!response.ok) throw new Error("Weather service unavailable.");
      setWeather(await response.json());
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to load weather."); } finally { setLoading(false); }
  }, [place, unit]);

  useEffect(() => { void load(); }, [load]);

  async function search(event: FormEvent) {
    event.preventDefault(); if (!query.trim()) return;
    setLoading(true); setMessage("");
    try {
      const response = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=1&language=en&format=json`);
      const data = await response.json(); if (!data.results?.[0]) throw new Error("No city found.");
      setPlace(data.results[0]); setQuery("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Search failed."); setLoading(false); }
  }

  const formatTemp = (value: number) => `${Math.round(value)}°`;
  const nowIndex = weather ? Math.max(0, weather.hourly.time.findIndex((time) => new Date(time).getTime() >= Date.now())) : 0;
  const hour = new Intl.DateTimeFormat("en", { hour: "numeric" });
  const date = new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" });

  return <main className="weather-shell"><header className="weather-header"><div className="weather-brand"><span className="brand-mark">◒</span>Atmos</div><form className="search" onSubmit={search}><span>⌕</span><input aria-label="Search city" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a city..." /><button>Search</button></form><div className="header-actions"><div className="units"><button className={unit === "celsius" ? "selected" : ""} onClick={() => setUnit("celsius")}>°C</button><button className={unit === "fahrenheit" ? "selected" : ""} onClick={() => setUnit("fahrenheit")}>°F</button></div></div></header><section className="weather-content"><div className="location-heading"><div><p className="eyebrow">Your forecast</p><h1>{place.name}</h1><p className="country">{place.country} · {place.timezone}</p></div><p className="updated">Live data from Open-Meteo</p></div>{message && <div className="error-banner">{message}</div>}{loading && !weather ? <div className="loading">Loading forecast...</div> : weather && <><section className="current-card"><div className="current-icon">{glyph(weather.current.weather_code)}</div><div><p className="eyebrow">Now</p><div className="current-temperature">{formatTemp(weather.current.temperature_2m)}<span>{weather.current_units.temperature_2m}</span></div><h2>{text(weather.current.weather_code)}</h2><p className="feels">Feels like {formatTemp(weather.current.apparent_temperature)} · {weather.current.is_day ? "Daylight" : "Night"}</p></div><div className="current-details"><div><span>Humidity</span><strong>{weather.current.relative_humidity_2m}%</strong></div><div><span>Wind</span><strong>{Math.round(weather.current.wind_speed_10m)} {weather.current_units.wind_speed_10m}</strong></div><div><span>Rain chance</span><strong>{weather.hourly.precipitation_probability[nowIndex] ?? 0}%</strong></div></div></section><section className="weather-section"><p className="eyebrow">Next 24 hours</p><h2>Hourly forecast</h2><div className="hourly-grid">{weather.hourly.time.slice(nowIndex, nowIndex + 8).map((time, index) => { const i = nowIndex + index; return <article className="hour-card" key={time}><span>{index ? hour.format(new Date(time)) : "Now"}</span><strong>{glyph(weather.hourly.weather_code[i])}</strong><b>{formatTemp(weather.hourly.temperature_2m[i])}</b><small>{weather.hourly.precipitation_probability[i]}% rain</small></article>; })}</div></section><section className="weather-section"><p className="eyebrow">Plan ahead</p><h2>7-day forecast</h2><div className="daily-list">{weather.daily.time.map((day, index) => <article className="day-row" key={day}><strong>{index ? date.format(new Date(`${day}T12:00:00`)) : "Today"}</strong><span className="day-icon">{glyph(weather.daily.weather_code[index])}</span><span className="day-description">{text(weather.daily.weather_code[index])}</span><span className="rain">{weather.daily.precipitation_probability_max[index]}% rain</span><b>{formatTemp(weather.daily.temperature_2m_max[index])} <em>{formatTemp(weather.daily.temperature_2m_min[index])}</em></b></article>)}</div></section></>}<footer>Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a> · No API key required</footer></section></main>;
}
