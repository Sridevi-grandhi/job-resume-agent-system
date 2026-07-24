import { useEffect, useState } from "react";
import "./App.css";

import {
  checkBackendHealth,
  signupUser,
  loginUser,
  saveUserPreferences,
  getUserPreferences,
  uploadResume,
  getLatestResume,
  searchJobs,
  searchUpdatedJobs,
  getJobs,
  calculateMatches,
  getMatches,
  optimizeResume,
  getOptimizedResumes,
  generateEmailSummary,
  getEmailLogs,
  runFullWorkflow
} from "./api";


function getEmptyPreferences() {
  return {
    job_title: "",
    location: "",
    experience: "",
    work_mode: "",
    job_type: "",
    skills: "",
    schedule_time: "08:00",
    timezone: "America/Chicago"
  };
}


function App() {
  const [backendStatus, setBackendStatus] = useState("Checking backend...");

  const [authMode, setAuthMode] = useState("login");
  const [authMessage, setAuthMessage] = useState("");

  const [signupForm, setSignupForm] = useState({
    name: "",
    email: "",
    password: ""
  });

  const [loginForm, setLoginForm] = useState({
    email: "",
    password: ""
  });

  const [currentUser, setCurrentUser] = useState(() => {
    const savedUser = localStorage.getItem("currentUser");

    if (savedUser) {
      return JSON.parse(savedUser);
    }

    return null;
  });

  const userId = currentUser?.user_id;

  const [preferences, setPreferences] = useState(getEmptyPreferences());
  const [preferencesSaved, setPreferencesSaved] = useState(false);
  const [preferenceMessage, setPreferenceMessage] = useState("");

  const [selectedResumeFile, setSelectedResumeFile] = useState(null);
  const [resumeMessage, setResumeMessage] = useState("");
  const [latestResume, setLatestResume] = useState(null);

  const [jobMessage, setJobMessage] = useState("");
  const [jobs, setJobs] = useState([]);

  const [optimizedMessage, setOptimizedMessage] = useState("");
  const [optimizedResumes, setOptimizedResumes] = useState([]);

  const [matchMessage, setMatchMessage] = useState("");
  const [matches, setMatches] = useState([]);

  const [emailMessage, setEmailMessage] = useState("");
  const [emailLogs, setEmailLogs] = useState([]);

  const [workflowMessage, setWorkflowMessage] = useState("");
  const [workflowResult, setWorkflowResult] = useState(null);


  useEffect(() => {
    handleBackendHealth();
  }, []);


  async function handleBackendHealth() {
    try {
      const data = await checkBackendHealth();
      setBackendStatus(data.status);
    } catch (error) {
      setBackendStatus("Backend not connected");
    }
  }


  function handleSignupChange(event) {
    const { name, value } = event.target;

    setSignupForm({
      ...signupForm,
      [name]: value
    });
  }


  function handleLoginChange(event) {
    const { name, value } = event.target;

    setLoginForm({
      ...loginForm,
      [name]: value
    });
  }


  async function handleSignup(event) {
    event.preventDefault();

    try {
      const data = await signupUser(signupForm);

      const userData = {
        user_id: data.user_id,
        email: data.email,
        name: signupForm.name
      };

      localStorage.setItem("currentUser", JSON.stringify(userData));
      setCurrentUser(userData);

      setAuthMessage("Signup successful");
      resetDashboardData();
    } catch (error) {
      setAuthMessage(error.message);
    }
  }


  async function handleLogin(event) {
    event.preventDefault();

    try {
      const data = await loginUser(loginForm);

      const userData = {
        user_id: data.user_id,
        email: data.email,
        name: data.name || ""
      };

      localStorage.setItem("currentUser", JSON.stringify(userData));
      setCurrentUser(userData);

      setAuthMessage("Login successful");
      resetDashboardData();
    } catch (error) {
      setAuthMessage(error.message);
    }
  }


  function handleLogout() {
    localStorage.removeItem("currentUser");
    setCurrentUser(null);

    setLoginForm({
      email: "",
      password: ""
    });

    setSignupForm({
      name: "",
      email: "",
      password: ""
    });

    resetDashboardData();
    setAuthMode("login");
    setAuthMessage("Logged out successfully");
  }


  function resetDashboardData() {
    setPreferences(getEmptyPreferences());
    setPreferencesSaved(false);
    setPreferenceMessage("");

    setSelectedResumeFile(null);
    setResumeMessage("");
    setLatestResume(null);

    setJobMessage("");
    setJobs([]);

    setOptimizedMessage("");
    setOptimizedResumes([]);

    setMatchMessage("");
    setMatches([]);

    setEmailMessage("");
    setEmailLogs([]);

    setWorkflowMessage("");
    setWorkflowResult(null);
  }


  function handlePreferenceChange(event) {
    const { name, value } = event.target;

    setPreferences({
      ...preferences,
      [name]: value
    });

    setPreferencesSaved(false);
    setPreferenceMessage("Please click Save Preferences after making changes.");
  }


  async function handleSavePreferences(event) {
    event.preventDefault();

    try {
      if (!userId) {
        setPreferenceMessage("Please login first.");
        return;
      }

      const data = await saveUserPreferences({
        ...preferences,
        user_id: Number(userId)
      });

      setPreferencesSaved(true);
      setPreferenceMessage(data.message);
    } catch (error) {
      setPreferencesSaved(false);
      setPreferenceMessage(error.message);
    }
  }


  async function handleLoadPreferences() {
    try {
      if (!userId) {
        setPreferenceMessage("Please login first.");
        return;
      }

      const data = await getUserPreferences(userId);

      setPreferences({
        job_title: data.job_title || "",
        location: data.location || "",
        experience: data.experience || "",
        work_mode: data.work_mode || "",
        job_type: data.job_type || "",
        skills: data.skills || "",
        schedule_time: data.schedule_time
          ? data.schedule_time.substring(0, 5)
          : "08:00",
        timezone: data.timezone || "America/Chicago"
      });

      setPreferencesSaved(false);
      setPreferenceMessage(
        "Preferences loaded. Please click Save Preferences before searching jobs."
      );
    } catch (error) {
      setPreferences(getEmptyPreferences());
      setPreferencesSaved(false);
      setPreferenceMessage(
        "No preferences saved yet for this user. Fill the form and click Save Preferences."
      );
    }
  }


  function handleResumeFileChange(event) {
    setSelectedResumeFile(event.target.files[0]);
  }


  async function handleUploadResume() {
    try {
      if (!userId) {
        setResumeMessage("Please login first.");
        return;
      }

      if (!selectedResumeFile) {
        setResumeMessage("Please choose a resume file first.");
        return;
      }

      const data = await uploadResume(userId, selectedResumeFile);

      setResumeMessage(data.message);
      setLatestResume(data);
    } catch (error) {
      setResumeMessage(error.message);
    }
  }


  async function handleLoadLatestResume() {
    try {
      if (!userId) {
        setResumeMessage("Please login first.");
        return;
      }

      const data = await getLatestResume(userId);

      setLatestResume(data);
      setResumeMessage("Latest resume loaded successfully.");
    } catch (error) {
      setLatestResume(null);
      setResumeMessage(error.message);
    }
  }


  async function handleSearchJobs() {
    try {
      if (!userId) {
        setJobMessage("Please login first.");
        return;
      }

      if (!preferencesSaved) {
        setJobMessage("Please save preferences first before searching jobs.");
        return;
      }

      const data = await searchJobs(userId);

      setJobMessage(data.message || "Jobs searched successfully.");
      await handleLoadJobs();
    } catch (error) {
      setJobMessage(error.message);
    }
  }


  async function handleSearchUpdatedJobs() {
    try {
      if (!userId) {
        setJobMessage("Please login first.");
        return;
      }

      if (!preferencesSaved) {
        setJobMessage("Please save preferences first before searching updated jobs.");
        return;
      }

      const data = await searchUpdatedJobs(userId);

      setJobMessage(data.message || "Updated jobs searched successfully.");
      await handleLoadJobs();
    } catch (error) {
      setJobMessage(error.message);
    }
  }


  async function handleLoadJobs() {
    try {
      if (!userId) {
        setJobMessage("Please login first.");
        return;
      }

      const data = await getJobs(userId);

      setJobs(Array.isArray(data) ? data : data.jobs || []);
      setJobMessage("Jobs loaded successfully.");
    } catch (error) {
      setJobs([]);
      setJobMessage(error.message);
    }
  }


  async function handleOptimizeResume(jobId) {
    try {
      if (!userId) {
        setOptimizedMessage("Please login first.");
        return;
      }

      const data = await optimizeResume(userId, jobId);

      setOptimizedMessage(data.message || "Resume optimized successfully.");
      await handleLoadOptimizedResumes();
    } catch (error) {
      setOptimizedMessage(error.message);
    }
  }


  async function handleLoadOptimizedResumes() {
    try {
      if (!userId) {
        setOptimizedMessage("Please login first.");
        return;
      }

      const data = await getOptimizedResumes(userId);

      setOptimizedResumes(
        Array.isArray(data) ? data : data.optimized_resumes || []
      );

      setOptimizedMessage("Optimized resumes loaded successfully.");
    } catch (error) {
      setOptimizedResumes([]);
      setOptimizedMessage(error.message);
    }
  }


  async function handleCalculateMatches() {
    try {
      if (!userId) {
        setMatchMessage("Please login first.");
        return;
      }

      const data = await calculateMatches(userId);

      setMatchMessage(data.message || "Matches calculated successfully.");
      await handleLoadMatches();
    } catch (error) {
      setMatchMessage(error.message);
    }
  }


  async function handleLoadMatches() {
    try {
      if (!userId) {
        setMatchMessage("Please login first.");
        return;
      }

      const data = await getMatches(userId);

      setMatches(Array.isArray(data) ? data : data.matches || []);
      setMatchMessage("Matches loaded successfully.");
    } catch (error) {
      setMatches([]);
      setMatchMessage(error.message);
    }
  }


  async function handleGenerateEmailSummary() {
    try {
      if (!userId) {
        setEmailMessage("Please login first.");
        return;
      }

      const data = await generateEmailSummary(userId);

      setEmailMessage(data.message || "Email summary generated successfully.");
      await handleLoadEmailLogs();
    } catch (error) {
      setEmailMessage(error.message);
    }
  }


  async function handleLoadEmailLogs() {
    try {
      if (!userId) {
        setEmailMessage("Please login first.");
        return;
      }

      const data = await getEmailLogs(userId);

      setEmailLogs(Array.isArray(data) ? data : data.email_logs || []);
      setEmailMessage("Email logs loaded successfully.");
    } catch (error) {
      setEmailLogs([]);
      setEmailMessage(error.message);
    }
  }


  async function handleRunFullWorkflow() {
    try {
      if (!userId) {
        setWorkflowMessage("Please login first.");
        return;
      }

      if (!preferencesSaved) {
        setWorkflowMessage("Please save preferences first before running full workflow.");
        return;
      }

      const data = await runFullWorkflow(userId);

      setWorkflowResult(data);
      setWorkflowMessage(data.message || "Full workflow completed successfully.");

      await handleLoadJobs();
      await handleLoadOptimizedResumes();
      await handleLoadMatches();
      await handleLoadEmailLogs();
    } catch (error) {
      setWorkflowMessage(error.message);
    }
  }


  if (!currentUser) {
    return (
      <div className="app">
        <header className="header">
          <h1>AI Job Search & Resume Optimization</h1>
          <p>Your personal job search automation dashboard</p>

          <div className="backend-status">
            Backend Status: {backendStatus}
          </div>
        </header>

        <main className="dashboard">
          <section className="card full-width">
            <h2>{authMode === "login" ? "Login" : "Signup"}</h2>
            <p>Please login or create an account to use your dashboard.</p>

            {authMode === "login" ? (
              <form className="form" onSubmit={handleLogin}>
                <div className="form-row">
                  <label>Email</label>
                  <input
                    type="email"
                    name="email"
                    value={loginForm.email}
                    onChange={handleLoginChange}
                    required
                  />
                </div>

                <div className="form-row">
                  <label>Password</label>
                  <input
                    type="password"
                    name="password"
                    value={loginForm.password}
                    onChange={handleLoginChange}
                    required
                  />
                </div>

                <div className="button-row">
                  <button type="submit">Login</button>

                  <button
                    type="button"
                    onClick={() => {
                      setAuthMode("signup");
                      setAuthMessage("");
                    }}
                  >
                    New user? Signup
                  </button>
                </div>
              </form>
            ) : (
              <form className="form" onSubmit={handleSignup}>
                <div className="form-row">
                  <label>Name</label>
                  <input
                    type="text"
                    name="name"
                    value={signupForm.name}
                    onChange={handleSignupChange}
                    required
                  />
                </div>

                <div className="form-row">
                  <label>Email</label>
                  <input
                    type="email"
                    name="email"
                    value={signupForm.email}
                    onChange={handleSignupChange}
                    required
                  />
                </div>

                <div className="form-row">
                  <label>Password</label>
                  <input
                    type="password"
                    name="password"
                    value={signupForm.password}
                    onChange={handleSignupChange}
                    required
                  />
                </div>

                <div className="button-row">
                  <button type="submit">Signup</button>

                  <button
                    type="button"
                    onClick={() => {
                      setAuthMode("login");
                      setAuthMessage("");
                    }}
                  >
                    Already have account? Login
                  </button>
                </div>
              </form>
            )}

            {authMessage && <div className="message">{authMessage}</div>}
          </section>
        </main>
      </div>
    );
  }


  return (
    <div className="app">
      <header className="header">
        <h1>AI Job Search & Resume Optimization</h1>
        <p>Your personal job search automation dashboard</p>

        <div className="backend-status">
          Backend Status: {backendStatus}
        </div>

        <nav className="nav">
          <span>
            Logged in as: {currentUser.email} | User ID: {currentUser.user_id}
          </span>

          <button type="button" onClick={handleLogout}>
            Logout
          </button>
        </nav>
      </header>

      <main className="dashboard">
        <section className="card full-width">
          <h2>1. Job Preferences</h2>
          <p>
            New users will not have saved preferences. Fill this form and click
            Save Preferences before searching jobs or running the workflow.
          </p>

          <form className="form" onSubmit={handleSavePreferences}>
            <div className="form-row">
              <label>Job Title</label>
              <input
                type="text"
                name="job_title"
                value={preferences.job_title}
                onChange={handlePreferenceChange}
                required
              />
            </div>

            <div className="form-row">
              <label>Location</label>
              <input
                type="text"
                name="location"
                value={preferences.location}
                onChange={handlePreferenceChange}
                required
              />
            </div>

            <div className="form-row">
              <label>Experience</label>
              <input
                type="text"
                name="experience"
                value={preferences.experience}
                onChange={handlePreferenceChange}
                required
              />
            </div>

            <div className="form-row">
              <label>Work Mode</label>
              <select
                name="work_mode"
                value={preferences.work_mode}
                onChange={handlePreferenceChange}
                required
              >
                <option value="">Select work mode</option>
                <option value="Remote">Remote</option>
                <option value="Hybrid">Hybrid</option>
                <option value="Onsite">Onsite</option>
              </select>
            </div>

            <div className="form-row">
              <label>Job Type</label>
              <select
                name="job_type"
                value={preferences.job_type}
                onChange={handlePreferenceChange}
                required
              >
                <option value="">Select job type</option>
                <option value="Full-time">Full-time</option>
                <option value="Contract">Contract</option>
                <option value="Part-time">Part-time</option>
              </select>
            </div>

            <div className="form-row">
              <label>Skills</label>
              <textarea
                name="skills"
                value={preferences.skills}
                onChange={handlePreferenceChange}
                rows="4"
                required
              />
            </div>

            <div className="form-row">
              <label>Schedule Time</label>
              <input
                type="time"
                name="schedule_time"
                value={preferences.schedule_time}
                onChange={handlePreferenceChange}
                required
              />
            </div>

            <div className="form-row">
              <label>Timezone</label>
              <input
                type="text"
                name="timezone"
                value={preferences.timezone}
                onChange={handlePreferenceChange}
                required
              />
            </div>

            <div className="button-row">
              <button type="submit">Save Preferences</button>

              <button type="button" onClick={handleLoadPreferences}>
                Load Saved Preferences
              </button>
            </div>
          </form>

          {preferenceMessage && (
            <div className="message">
              {preferenceMessage}
            </div>
          )}

          <p>
            Current save status:{" "}
            <strong>{preferencesSaved ? "Saved" : "Not saved / changed"}</strong>
          </p>
        </section>

        <section className="card">
          <h2>2. Resume Upload</h2>
          <p>Upload your resume as PDF, DOCX, or TXT.</p>

          <input
            type="file"
            accept=".pdf,.docx,.txt"
            onChange={handleResumeFileChange}
          />

          <div className="button-row">
            <button type="button" onClick={handleUploadResume}>
              Upload Resume
            </button>

            <button type="button" onClick={handleLoadLatestResume}>
              Load Latest Resume
            </button>
          </div>

          {resumeMessage && <div className="message">{resumeMessage}</div>}

          {latestResume && (
            <div className="result-box">
              <h3>Latest Resume</h3>

              <p>
                <strong>File:</strong>{" "}
                {latestResume.file_name || latestResume.original_file_name}
              </p>

              <p>
                <strong>Type:</strong> {latestResume.file_type}
              </p>

              <pre>
                {latestResume.text_preview || latestResume.parsed_text}
              </pre>
            </div>
          )}
        </section>

        <section className="card">
          <h2>3. Job Search Agent</h2>
          <p>
            Use Search Jobs for the existing job-link agent. Use Search Updated
            Jobs for the Remotive API real-job agent.
          </p>

          <div className="button-row">
            <button type="button" onClick={handleSearchJobs}>
              Search Jobs
            </button>

            <button type="button" onClick={handleSearchUpdatedJobs}>
              Search Updated Jobs
            </button>

            <button type="button" onClick={handleLoadJobs}>
              Load Jobs
            </button>
          </div>

          {jobMessage && <div className="message">{jobMessage}</div>}

          {jobs.length > 0 && (
            <div className="result-box">
              <h3>Jobs</h3>

              {jobs.map((job) => (
                <div className="job-item" key={job.id}>
                  <h4>{job.title}</h4>

                  <p>
                    <strong>Company:</strong> {job.company}
                  </p>

                  <p>
                    <strong>Location:</strong> {job.location}
                  </p>

                  <p>
                    <strong>Work Mode:</strong> {job.work_mode}
                  </p>

                  <p>
                    <strong>Job Type:</strong> {job.job_type}
                  </p>

                  <p>
                    <strong>Source:</strong> {job.source}
                  </p>

                  <p>
                    <strong>Skills:</strong> {job.skills_required}
                  </p>

                  <p>
                    <strong>Description:</strong> {job.description}
                  </p>

                  {job.job_url && (
                    <p>
                      <strong>URL:</strong>{" "}
                      <a href={job.job_url} target="_blank" rel="noreferrer">
                        {job.job_url}
                      </a>
                    </p>
                  )}

                  <button
                    type="button"
                    onClick={() => handleOptimizeResume(job.id)}
                  >
                    Optimize Resume for This Job
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card">
          <h2>4. Resume Optimization Agent</h2>
          <p>
            First optimize your resume for a selected job, then calculate the match percentage.
          </p>

          <button type="button" onClick={handleLoadOptimizedResumes}>
            Load Optimized Resumes
          </button>

          {optimizedMessage && (
            <div className="message">{optimizedMessage}</div>
          )}

          {optimizedResumes.length > 0 && (
            <div className="result-box">
              <h3>Optimized Resumes</h3>

              {optimizedResumes.map((resume) => (
                <div className="job-item" key={resume.id}>
                  <p>
                    <strong>Job ID:</strong> {resume.job_id}
                  </p>

                  <pre>{resume.optimized_text}</pre>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card">
          <h2>5. Matching Percentage Agent</h2>
          <p>
            Calculate match percentage after jobs are searched and resume is optimized.
            Results show the job title, company, source, URL, matched keywords,
            missing keywords, and recommendation.
          </p>

          <div className="button-row">
            <button type="button" onClick={handleCalculateMatches}>
              Calculate Matches
            </button>

            <button type="button" onClick={handleLoadMatches}>
              Load Matches
            </button>
          </div>

          {matchMessage && <div className="message">{matchMessage}</div>}

          {matches.length > 0 && (
            <div className="result-box">
              <h3>Matches</h3>

              {matches.map((match) => (
                <div className="job-item" key={match.id}>
                  <h4>{match.job_title}</h4>

                  <p>
                    <strong>Company:</strong> {match.company}
                  </p>

                  <p>
                    <strong>Location:</strong> {match.location}
                  </p>

                  <p>
                    <strong>Source:</strong> {match.source}
                  </p>

                  <p>
                    <strong>Job ID:</strong> {match.job_id}
                  </p>

                  <p>
                    <strong>Match:</strong> {match.match_percentage}%
                  </p>

                  <p>
                    <strong>Matched Keywords:</strong> {match.matched_keywords}
                  </p>

                  <p>
                    <strong>Missing Keywords:</strong> {match.missing_keywords}
                  </p>

                  <p>
                    <strong>Recommendation:</strong> {match.recommendation}
                  </p>

                  {match.job_url && (
                    <p>
                      <strong>Job URL:</strong>{" "}
                      <a href={match.job_url} target="_blank" rel="noreferrer">
                        {match.job_url}
                      </a>
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card">
          <h2>6. Email Agent</h2>
          <p>Generate and view daily email summaries.</p>

          <div className="button-row">
            <button type="button" onClick={handleGenerateEmailSummary}>
              Generate Email Summary
            </button>

            <button type="button" onClick={handleLoadEmailLogs}>
              Load Email Logs
            </button>
          </div>

          {emailMessage && <div className="message">{emailMessage}</div>}

          {emailLogs.length > 0 && (
            <div className="result-box">
              <h3>Email Logs</h3>

              {emailLogs.map((email) => (
                <div className="job-item" key={email.id}>
                  <p>
                    <strong>To:</strong> {email.recipient_email}
                  </p>

                  <p>
                    <strong>Subject:</strong> {email.subject}
                  </p>

                  <p>
                    <strong>Status:</strong> {email.status}
                  </p>

                  <pre>{email.body}</pre>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card full-width">
          <h2>7. Full Workflow</h2>
          <p>
            Runs Job Search, Resume Optimization, Matching, and Email Summary.
            You must save preferences before running this.
          </p>

          <button type="button" onClick={handleRunFullWorkflow}>
            Run Full Workflow
          </button>

          {workflowMessage && (
            <div className="message">{workflowMessage}</div>
          )}

          {workflowResult && (
            <div className="result-box">
              <h3>Workflow Result</h3>
              <pre>{JSON.stringify(workflowResult, null, 2)}</pre>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;