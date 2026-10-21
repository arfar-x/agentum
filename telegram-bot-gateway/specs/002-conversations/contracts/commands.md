# Contract: Bot commands

Every command works in a private chat; in a group it acts on the sender's own session. A command addressed to another bot (`/cmd@otherbot`) is ignored. The menu below is registered with Telegram at start (capability 006).

| Command | Needs a link | Effect and reply |
|---|---|---|
| `/start [username]` | no | See `001-account-linking/contracts/operator-cli.md`, section Bot messages |
| `/new` | yes | Cancels as `/stop` does, deletes the session; `Started a new conversation.` |
| `/stop` | no | `Stopped.` when something was cancelled or discarded, else `Nothing is running.` |
| `/whoami` | no | Three lines: `Telegram id: <id>`, `Account: linked as <username>` or `Account: not linked`, `Session: <session key>`; never a key |
| `/help` | no | The help text below |
| `/model [name]` | yes | With a name: `model = <name>`. Without: buttons for up to 40 models, text `Choose a model:` |
| `/reasoning [level]` | yes | With a level: `reasoning = <level>`, or `reasoning reset to the endpoint's default` for `default`. Without: buttons none, minimal, low / medium, high, default, text `Reasoning effort:` |
| `/set <name> <value>` | yes | `<name> = <value>`; with fewer than two arguments: `usage: /set <name> <value>; names: temperature, top_p, max_tokens, seed, verbosity, presence_penalty, frequency_penalty, stop` |
| `/unset <name>` | yes | `<name> reset to the endpoint's default`; with other than one argument: `usage: /unset <name>`; unknown name: `unknown setting '<name>'` |
| `/settings` | yes | The model line and one line per setting, with a Reset button |

Reset button: clears the model and all settings, answers `Settings reset.`

## Validation replies

| Case | Reply |
|---|---|
| Unknown setting | `unknown setting '<name>'; choose from <names>` |
| No value | `give a value for <name>` |
| Not a number | `<name> must be a number` (`a whole number` for max_tokens and seed) |
| Out of range | `<name> must be between <low> and <high>` (`at least <low>` when there is no upper bound) |
| Bad verbosity | `verbosity must be one of low, medium, high` |
| Bad stop | `stop must be text or a JSON list of text`, or `stop takes one to four strings` |
| Bad reasoning level | `reasoning must be one of none, minimal, low, medium, high` |

## Help text

```text
Send me a message and I answer it with your own account.

/new  start a fresh conversation
/stop  cancel what I am working on
/model  pick the model
/reasoning  set the reasoning effort
/set <name> <value>, /unset <name>  tune the model (temperature, top_p, max_tokens, seed, verbosity, presence_penalty, frequency_penalty, stop)
/settings  show what is set
/whoami  who I think you are
```

## Menu registered with Telegram

| Command | Description |
|---|---|
| new | Start a new conversation |
| stop | Cancel the running request |
| model | Choose the model |
| reasoning | Set the reasoning effort |
| set | Set a generation parameter |
| unset | Reset a generation parameter |
| settings | Show current settings |
| whoami | Show who you are linked as |
| help | What I can do |

## Buttons

| Callback data | Sent by | Effect |
|---|---|---|
| `m:<i>` | `/model` | Refetches the model list with the user's key, sets the model at position *i*, answers with a short notice `model = <name>` and edits the message to that text; an error or an index no longer in the list is shown as an alert |
| `r:<level>` | `/reasoning` | Sets the reasoning effort as `/reasoning <level>` does and edits the message to the reply |
| `s:reset` | `/settings` | Clears the model and all settings, answers and edits the message to `Settings reset.` |
| `a:<i>`, `a:o` | a question card | See `005-questions-and-approvals/contracts/clarify-tool.md` |

A button of the first three kinds pressed by an unlinked user answers with the alert `Link your account first.` The settings buttons act on the session of the person who pressed them. If listing the models fails, `/model` replies with the endpoint's error text instead of buttons.
