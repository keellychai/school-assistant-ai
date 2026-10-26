
document.addEventListener("DOMContentLoaded", () => {
    const loginForm = document.getElementById("loginForm");

    if (loginForm) {
        loginForm.addEventListener("submit", (event) => {
            event.preventDefault();

            if (loginForm.reportValidity()) {
                const studentNameInput = document.getElementById("studentName");

                if (!studentNameInput) {
                    alert("Student name input was not found.");
                    return;
                }

                const studentName = studentNameInput.value.trim();

                if (!studentName) {
                    alert("Please enter your student name.");
                    studentNameInput.focus();
                    return;
                }

                sessionStorage.setItem("studentName", studentName);
                window.location.href = "/dashboard";
            }
        });
    }

    const dashboardStudentName = document.getElementById("dashboardStudentName");

    if (dashboardStudentName) {
        dashboardStudentName.textContent =
            sessionStorage.getItem("studentName") || "Student";
    }

    const demoChatButton = document.getElementById("demoChatButton");

    if (demoChatButton) {
        demoChatButton.addEventListener("click", () => {
            window.location.href = "/chatbot";
        });
    }

    const chatForm = document.getElementById("chatForm");
    const questionInput = document.getElementById("questionInput");
    const chatMessages = document.getElementById("chatMessages");
    const sendButton = document.getElementById("sendButton");
    const clearChatButton = document.getElementById("clearChatButton");

    function addMessage(text, type, source = null, needsReview = false) {
        if (!chatMessages) {
            return;
        }

        const row = document.createElement("div");
        row.className = `message-row ${type}-row`;

        if (type === "assistant") {
            const avatar = document.createElement("div");
            avatar.className = "message-avatar";
            avatar.textContent = "✳";
            row.appendChild(avatar);
        }

        const bubble = document.createElement("div");
        bubble.className = "message-bubble";

        const paragraph = document.createElement("p");
        paragraph.textContent = text;
        bubble.appendChild(paragraph);

        if (source) {
            const sourceCard = document.createElement("div");
            sourceCard.className = "source-card";

            const label = document.createElement("span");
            label.textContent = "Source";

            const title = document.createElement("strong");
            title.textContent = `${source.title} · ${source.id}`;

            sourceCard.append(label, title);
            bubble.appendChild(sourceCard);
        }

        if (needsReview) {
            const note = document.createElement("div");
            note.className = "review-note";
            note.textContent =
                "Information not confirmed. Please verify with school staff.";
            bubble.appendChild(note);
        }

        const time = document.createElement("span");
        time.className = "message-time";
        time.textContent =
            type === "assistant" ? "SchoolSync Assistant" : "You";

        bubble.appendChild(time);
        row.appendChild(bubble);
        chatMessages.appendChild(row);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function askQuestion(question) {
        if (
            !question ||
            !chatForm ||
            !questionInput ||
            !chatMessages ||
            !sendButton ||
            sendButton.disabled
        ) {
            return;
        }

        addMessage(question, "user");
        questionInput.value = "";
        sendButton.disabled = true;

        const loadingRow = document.createElement("div");
        loadingRow.className = "message-row assistant-row";

        const loadingBubble = document.createElement("div");
        loadingBubble.className = "message-bubble";
        loadingBubble.textContent = "Checking school information...";

        loadingRow.appendChild(loadingBubble);
        chatMessages.appendChild(loadingRow);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        try {
            const response = await fetch("/api/ask", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ question })
            });

            const result = await response.json();

            loadingRow.remove();

            if (!response.ok) {
                throw new Error(
                    result.error || "Unable to process the question."
                );
            }

            addMessage(
                result.answer,
                "assistant",
                result.source,
                result.needs_review
            );
        } catch (error) {
            loadingRow.remove();

            addMessage(
                error.message || "Something went wrong. Please try again.",
                "assistant",
                null,
                true
            );
        } finally {
            sendButton.disabled = false;
            questionInput.focus();
        }
    }

    if (chatForm && questionInput && chatMessages && sendButton) {
        chatForm.addEventListener("submit", (event) => {
            event.preventDefault();

            const question = questionInput.value.trim();

            if (question) {
                askQuestion(question);
            }
        });

        document.querySelectorAll(".suggestion-chip").forEach((button) => {
            button.addEventListener("click", () => {
                askQuestion(button.textContent.trim());
            });
        });

        if (clearChatButton) {
            clearChatButton.addEventListener("click", () => {
                chatMessages.replaceChildren();

                addMessage(
                    "Hello! I'm your SchoolSync AI Assistant. Ask me about school schedules, announcements, and administrative procedures.",
                    "assistant"
                );

                questionInput.focus();
            });
        }
    }
});
