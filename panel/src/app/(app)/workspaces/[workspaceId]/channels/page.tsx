"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Badge, Button, Card, ErrorText, Input, Spinner } from "@/components/ui";

type ChannelCredentialOut = components["schemas"]["ChannelCredentialOut"];
type Channel = components["schemas"]["Channel"];

const CONNECTED_CHANNELS: Channel[] = ["wordpress", "telegram"];

export default function ChannelsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { t } = useLocale();
  const [credentials, setCredentials] = useState<ChannelCredentialOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [channel, setChannel] = useState<Channel>("wordpress");
  const [label, setLabel] = useState("default");
  const [busy, setBusy] = useState(false);

  const [siteUrl, setSiteUrl] = useState("");
  const [username, setUsername] = useState("");
  const [applicationPassword, setApplicationPassword] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");

  async function load() {
    try {
      const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/credentials", {
        params: { path: { workspace_id: workspaceId } },
      });
      setCredentials(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  useEffect(() => {
    // Fetch-on-mount: `load` is also called after each mutation below, so it
    // stays a named function rather than being inlined into the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload =
        channel === "wordpress"
          ? { site_url: siteUrl, username, application_password: applicationPassword }
          : { bot_token: botToken, chat_id: chatId };
      const publicMetadata = channel === "wordpress" ? { site_url: siteUrl } : { chat_id: chatId };
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/credentials", {
        params: { path: { workspace_id: workspaceId } },
        body: { channel, label, payload, public_metadata: publicMetadata },
      });
      setSiteUrl("");
      setUsername("");
      setApplicationPassword("");
      setBotToken("");
      setChatId("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(credentialId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiClient.DELETE("/api/v1/workspaces/{workspace_id}/credentials/{credential_id}", {
        params: { path: { workspace_id: workspaceId, credential_id: credentialId } },
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-lg font-semibold">{t("channels.title")}</h1>

      <Card className="mb-4">
        <form onSubmit={onCreate} className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <select
              value={channel}
              onChange={(event) => setChannel(event.target.value as Channel)}
              className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
            >
              {CONNECTED_CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <Input
              placeholder={t("channels.label")}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              className="max-w-40"
            />
          </div>

          {channel === "wordpress" ? (
            <div className="grid gap-2 sm:grid-cols-3">
              <Input placeholder="site_url" value={siteUrl} onChange={(event) => setSiteUrl(event.target.value)} required />
              <Input placeholder="username" value={username} onChange={(event) => setUsername(event.target.value)} required />
              <Input
                placeholder="application_password"
                type="password"
                value={applicationPassword}
                onChange={(event) => setApplicationPassword(event.target.value)}
                required
              />
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <Input placeholder="bot_token" type="password" value={botToken} onChange={(event) => setBotToken(event.target.value)} required />
              <Input placeholder="chat_id" value={chatId} onChange={(event) => setChatId(event.target.value)} required />
            </div>
          )}

          <div>
            <Button type="submit" disabled={busy}>
              {busy ? <Spinner /> : null}
              {t("channels.new")}
            </Button>
          </div>
        </form>
      </Card>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {credentials === null && !error ? <Spinner /> : null}
      {credentials?.length === 0 ? <p className="text-sm text-zinc-500">{t("channels.empty")}</p> : null}

      <div className="flex flex-col gap-2">
        {credentials?.map((credential) => (
          <Card key={credential.id} className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">
                {credential.channel} · {credential.label}
              </p>
              <p className="text-xs text-zinc-500">{JSON.stringify(credential.public_metadata)}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={credential.is_active ? "good" : "neutral"}>
                {credential.is_active ? t("channels.active") : t("channels.inactive")}
              </Badge>
              {credential.is_active ? (
                <Button type="button" variant="danger" disabled={busy} onClick={() => deactivate(credential.id)}>
                  {t("channels.deactivate")}
                </Button>
              ) : null}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
